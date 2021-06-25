import csv
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Tuple, Union

import pandas as pd
from pulp import LpMaximize, LpProblem, LpVariable, lpSum, PULP_CBC_CMD

from dfs import constraints
from dfs import data_frame_utils, pulp_utils
from dfs import file_utils
from dfs.slate import Slate
from dfs.exceptions import InvalidDataFrameException, UnsolvableLineupException, InvalidConstraintException
from dfs.positions import RB, WR, TE, FLEX, normalize_position

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class OptimizedLineup:
    """
    A class that represents an optimized fantasy football lineup for a given site.
    """

    def __init__(self, optimizer: 'LineupOptimizer', site: str):
        """
        :param optimizer: The optimizer used to generate this lineup.
        :param site: The fantasy site that the lineup has been generated for.
        """
        self.site = site
        players = optimizer.data[optimizer.data.apply(lambda x: x['LpVariable'].varValue == 1, axis=1)]
        self.points = round(players[optimizer.points_col].sum(), 2)
        self.salary = players[optimizer.salary_col].sum()
        col_mapping = {
            optimizer.id_col: 'id',
            optimizer.name_col: 'name',
            optimizer.position_col: 'position',
            optimizer.team_col: 'team',
            optimizer.opponent_col: 'opponent',
            optimizer.points_col: 'points',
            optimizer.salary_col: 'salary',
            optimizer.datetime_col: 'datetime'
        }
        players_dict = players.to_dict('records')
        position_to_count = dict()
        for p in players_dict:
            for k, v in col_mapping.items():
                if k is not None:  # id_col may be None
                    p[v] = p.pop(k)
            keys_to_delete = []
            for k in p.keys():
                if k not in col_mapping.values():
                    keys_to_delete.append(k)
            for k in keys_to_delete:
                del p[k]
            if p['position'] in position_to_count:
                position_to_count[p['position']] = position_to_count[p['position']] + 1
            else:
                position_to_count[p['position']] = 1
        self.players = [LineupPlayer(p) for p in players_dict]
        for position in (RB, WR, TE):
            _, maximum = optimizer.position_constraints()[position]
            if position_to_count[position] == maximum:
                logger.info(f"Flex position for this lineup is filled by {position}")
                players_for_position = list(sorted(filter(lambda player: player.position == position, self.players),
                                                   key=lambda x: x.datetime))
                players_for_position[-1].lineup_position = FLEX
                break

    def write_to_file(self, file_path: str) -> None:
        """
        Writes the optimized lineup to a CSV file.

        :param file_path: the path to the file which will be created if it does not exist.
        :return: None
        :raises: ValueError if file_path is None or points to a non-CSV file.
        """
        if file_path is None:
            raise ValueError('File path cannot be none')
        extension = file_utils.get_extension(file_path)
        if extension != 'csv':
            raise ValueError(f"Only CSV output is supported, found: {extension}")
        file_exists = file_utils.file_exists(file_path)
        with open(file_path, mode='a') as f:
            writer = csv.DictWriter(f, fieldnames=dir(self.players[0]))
            if not file_exists:
                writer.writeheader()
            writer.writerows([{k: player.__getattribute__(k) for k in dir(player)} for player in self.players])

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert this optimized lineup into a dict.

        :return: A dict representing this lineup.
        """
        return {
            'site': self.site,
            'points': self.points,
            'salary': self.salary,
            'players': [p.to_dict() for p in self.players]
        }

    def __repr__(self):
        return f"dfs.optimize.OptimizedLineup(site={self.site}, points={self.points}, salary={self.salary}, players={self.players})"

    def __str__(self):
        players_string = '\n'.join([str(p) for p in self.players])
        return (f"Optimized {self.site} Lineup \n"
                f"{self.points} points @ {self.salary} salary \n" +
                players_string)


class LineupPlayer:
    """
    A model of a player included in an optimized lineup.
    """

    def __init__(self, player_dict: dict):
        """
        Initializer.

        :param player_dict: the player dict corresponding to a row from the dataframe.
        """
        self.name = player_dict['name']
        self.position = player_dict['position']
        self.lineup_position = player_dict['position']
        self.team = player_dict['team']
        self.opponent = player_dict['opponent']
        self.points = player_dict['points']
        self.salary = player_dict['salary']
        self.datetime = player_dict['datetime']

    def to_dict(self) -> Dict[str, Any]:
        """
        Converts this lineup player into a dict.

        :return: A dict representing this player.
        """
        return {
            'name': self.name,
            'position': self.position,
            'lineup_position': self.lineup_position,
            'team': self.team,
            'opponent': self.opponent,
            'points': self.points,
            'salary': self.salary,
            'datetime': str(self.datetime)
        }

    def __dir__(self):
        return ['name', 'position', 'team', 'opponent', 'points', 'salary', 'datetime']

    def __repr__(self):
        return f"dfs.optimize.LineupPlayer(name={self.name}, position={self.position}, lineup_position={self.lineup_position}, team={self.team}, opponent={self.opponent}, points={self.points}, salary={self.salary}, datetime={self.datetime})"

    def __str__(self):
        return f"{self.lineup_position} {self.name} - {self.team} - {self.points} @ {self.salary}"


class LineupOptimizer(ABC):
    """
    A pandas data frame-based fantasy football lineup optimizer.
    This class is used to generate optimal fantasy football lineups for various sites when provided
    a data frame containing player, position, salary and points information.
    """

    def __init__(self,
                 data_source: Union[pd.DataFrame, str],
                 name_col: str = 'name',
                 points_col: str = 'points',
                 position_col: str = 'position',
                 salary_col: str = 'salary',
                 team_col: str = 'team',
                 opponent_col: str = 'opponent',
                 datetime_col: str = 'datetime',
                 id_col: str = None):
        """
        :param data_source: A dataframe or file path containing fantasy data.
        :param name_col: The player name column. Default is 'name'.
        :param points_col: The fantasy points column. Default is 'points'.
        :param position_col: The player position column. Default is 'position'.
        :param salary_col: The player salary column. Default is 'salary'.
        :param team_col: The player team column. Default is 'team'.
        :param opponent_col: The player opponent column. Default is 'opponent'.
        :param datetime_col: The datetime column. Default is 'datetime'.
        :param id_col: Optional ID column name.
        """
        if type(data_source) is pd.DataFrame:
            self._data = data_source.copy()  # don't impact original dataframe
        elif type(data_source) is str:
            if not file_utils.file_exists(file=data_source):
                raise ValueError('The data source file does not exist!')
            extension = file_utils.get_extension(file_path=data_source)
            if extension == 'csv':
                self._data = pd.read_csv(filepath_or_buffer=data_source)
            elif extension == 'xlsx':
                self._data = pd.read_excel(io=data_source, engine='openpyxl')
            else:
                raise ValueError('Invalid data source file path! csv and xlsx are supported.')
        else:
            raise ValueError('Invalid data source type!')
        if not all(c in self._data.columns for c in [name_col,
                                                     points_col,
                                                     position_col,
                                                     salary_col,
                                                     team_col,
                                                     opponent_col,
                                                     datetime_col]):
            raise InvalidDataFrameException('DataFrame does not contain necessary columns')
        if id_col is not None:
            if len(self._data[id_col].unique()) != len(self._data):
                raise InvalidDataFrameException('Provided ID column must be unique for each row')
        self._name_col = name_col
        self._points_col = points_col
        self._position_col = position_col
        self._salary_col = salary_col
        self._team_col = team_col
        self._opponent_col = opponent_col
        self._datetime_col = datetime_col
        self._id_col = id_col
        self._constraints = []
        self._data[self._position_col] = self._data[self._position_col].apply(lambda x: normalize_position(x))
        self._data.dropna(inplace=True)

    @property
    def data(self):
        return self._data

    @property
    def id_col(self):
        return self._id_col

    @property
    def name_col(self):
        return self._name_col

    @property
    def points_col(self):
        return self._points_col

    @property
    def position_col(self):
        return self._position_col

    @property
    def salary_col(self):
        return self._salary_col

    @property
    def team_col(self):
        return self._team_col

    @property
    def opponent_col(self):
        return self._opponent_col

    @property
    def datetime_col(self):
        return self._datetime_col

    @abstractmethod
    def num_players(self) -> int:
        """
        Returns the total number of players that are to be included in an optimized lineup for the given site.

        :return: The total number of players to be included in the lineup.
        """
        raise NotImplementedError

    @abstractmethod
    def salary_cap(self) -> int:
        """
        Returns the salary cap - or max available salary - to use for this given site's lineup optimization.

        :return: The site's salary cap.
        """
        raise NotImplementedError

    @abstractmethod
    def site_name(self) -> str:
        """
        Returns the name of the fantasy site that this lineup optimizer is being used for. Ex. DraftKings, FanDuel, etc.

        :return: The name of the fantasy site.
        """
        raise NotImplementedError

    @abstractmethod
    def position_constraints(self) -> Dict[str, Tuple[int, int]]:
        """
        Returns a dict that maps position to a tuple containing min and max numbers of this position that may be
        included in the optimized lineup. Ex. { RB : (2, 3) ... }

        :return: A dict mapping position name to min/max count.
        """
        raise NotImplementedError

    def set_only_include_teams(self, teams: List[str]) -> None:
        """
        Sets the teams that are to be considered for the lineup optimization.

        :param teams: The list of teams to consider.
        :return: None
        :raises: ValueError if teams to include is none or empty.
        """
        if teams is None or len(teams) == 0:
            raise ValueError('Included teams must not be none or empty')
        self._add_constraint(constraints.OnlyIncludeTeamsConstraint(teams=teams,
                                                                    team_column=self._team_col))

    def set_exclude_teams(self, teams: List[str]) -> None:
        """
        Sets the list of teams whose players are to be excluded from lineup optimization.

        :param teams: The list of teams to exclude.
        :return: None
        :raises: ValueError if teams to exclude is none or empty.
        """
        if teams is None or len(teams) == 0:
            raise ValueError('Teams to exclude must not be none or empty')
        for team in teams:
            self.set_max_players_from_team(n=0, team=team)

    def set_must_include_team(self, team: str):
        """
        Specifies that a lineup must include a player from a given team.

        :param team: The team that the lineup must include.
        :return: None
        :raises: ValueError if team is none or not found in data frame.
        """
        self.set_min_players_from_team(n=1, team=team)

    def set_must_include_player(self, **kwargs) -> None:
        """
        Specifies that a lineup must include a player identified by either name or id.
        Either name or id must be provided in kwargs.

        :return: None
        :raises: ValueError if the player is None or not found in the dataframe
        """
        if all([it not in kwargs for it in ['id', 'name']]):
            raise ValueError('Must provide id or name')
        if 'id' in kwargs and self._id_col is None:
            raise ValueError('ID column not specified')
        key, col = (kwargs['id'], self._id_col) if 'id' in kwargs else (kwargs['name'], self.name_col)
        if key is None or key not in self._data[col].unique():
            raise ValueError(f"{key} not found in data frame's {col} column")
        self._add_constraint(constraints.IncludePlayerConstraint(player=key,
                                                                 name_col=col))

    def set_exclude_player(self, **kwargs) -> None:
        """
        Specifies that a lineup must exclude a player identified by name.

        :return: None
        :raises: ValueError if player is None or not found in dataframe
        """
        if all([it not in kwargs for it in ['id', 'name']]):
            raise ValueError('Must provide id or name')
        if 'id' in kwargs and self._id_col is None:
            raise ValueError('ID column not specified')
        key, col = (kwargs['id'], self._id_col) if 'id' in kwargs else (kwargs['name'], self.name_col)
        if key is None or key not in self._data[col].unique():
            raise ValueError(f"{key} not found in data frame's {col} column")
        self._add_constraint(constraints.ExcludePlayerConstraint(player=key,
                                                                 name_col=col))

    def set_num_players_from_team(self, n: int, team: str):
        """
        Sets the number of players from a team that an optimized lineup must include.

        :param n: the number of players
        :param team: the team name
        :return: None
        :raises: ValueError if number of players or team are invalid
        """
        if n is None or n > self.num_players():
            raise ValueError('Invalid number of players')
        if team is None or team not in self._data[self._team_col].unique():
            raise ValueError('Invalid team name')
        self._add_constraint(constraints.MaxPlayersFromTeamConstraint(maximum=n,
                                                                      team=team,
                                                                      team_col=self._team_col))
        try:
            self._add_constraint(constraints.MinPlayersFromTeamConstraint(minimum=n,
                                                                          team=team,
                                                                          team_col=self._team_col))
        except InvalidConstraintException:
            self._constraints.pop()  # remove max players constraint if this one fails
            raise

    def set_max_players_from_team(self, n: int, team: str) -> None:
        """
        Sets the maximum number of players that can be included in an optimized lineup from a particular team.

        :param n: the maximum number of players that can be included from a particular team
        :param team: the name of the team
        :return: None
        :raises: ValueError if maximum or team are invalid
        """
        if n is None or n < 0:
            raise ValueError('Invalid maximum players')
        if team is None or team not in self._data[self._team_col].unique():
            raise ValueError('Invalid team name')
        self._add_constraint(constraints.MaxPlayersFromTeamConstraint(maximum=n,
                                                                      team=team,
                                                                      team_col=self._team_col))

    def set_min_players_from_team(self, n: int, team: str) -> None:
        """
        Sets the minimum number of players from a given team that must be included in an optimized lineup.

        :param n: the minimum number of players from the specified team that must be included
        :param team: the name of the team
        :return: None
        :raises: ValueError if minimum or team are invalid
        """
        if n is None or n > self.num_players():
            raise ValueError('Invalid minimum number of players')
        if team is None or team not in self._data[self._team_col].unique():
            raise ValueError('Invalid team name')
        if n == 0:
            return
        self._add_constraint(constraints.MinPlayersFromTeamConstraint(minimum=n,
                                                                      team=team,
                                                                      team_col=self._team_col))

    def set_max_salary(self, n: int) -> None:
        """
        Sets the maximum salary that can be used in an optimized lineup.

        :param n: the max salary.
        :return: None
        :raises: ValueError if maximum is invalid
        """
        if n is None or n <= 0:
            raise ValueError('Invalid maximum')
        self._add_constraint(constraints.MaxSalaryCapConstraint(salary=n,
                                                                salary_col=self._salary_col))

    def set_min_salary(self, n: int) -> None:
        """
        Sets the minimum salary that can be used in an optimized lineup.

        :param n: the minimum salary
        :return: None
        :raises: ValueError if minimum is invalid
        """
        if n is None or n > self.salary_cap():
            raise ValueError('Invalid minimum')
        self._add_constraint(constraints.MinSalaryCapConstraint(salary=n,
                                                                salary_col=self._salary_col))

    def set_game_slate_sunday(self) -> None:
        """
        Sets the optimizer to include all Sunday games only.

        :return: None
        """
        logger.info('Setting game slate to "Sunday"')
        self._set_game_slate(slate=Slate.SUNDAY)

    def set_game_slate_sunday_early(self) -> None:
        """
        Sets the optimizer to include only Sunday early games (13PM EST).

        :return: None
        """
        logger.info('Setting game slate to "Sunday early"')
        self._set_game_slate(slate=Slate.SUNDAY_EARLY)

    def set_game_slate_sunday_early_and_late(self) -> None:
        """
        Sets the optimizer to include only Sunday early and late games (13PM and 16PM EST).

        :return: None
        """
        logger.info('Setting game slate to "Sunday early and late"')
        self._set_game_slate(slate=Slate.SUNDAY_EARLY_AND_LATE)

    def set_game_slate_sunday_and_monday(self) -> None:
        """
        Sets the optimizer to include games taking place on Sunday and Monday.

        :return: None
        """
        logger.info('Setting game slate to "Sunday and Monday"')
        self._set_game_slate(slate=Slate.SUNDAY_AND_MONDAY)

    def _set_game_slate(self, slate: Slate) -> None:
        """
        Set the game slate to determine which games are to be included in an optimized lineup.

        :param slate: The game slate to include.
        :return: None
        """
        logger.warning(f"Setting game slate to {slate.name}")
        self._add_constraint(constraints.GameSlateConstraint(slate=slate,
                                                             datetime_col=self._datetime_col,
                                                             num_players=self.num_players()))

    def _add_constraint(self, constraint: constraints.LineupConstraint) -> None:
        """
        Internal method used to add a constraint by first checking if it is valid.

        :param constraint: The constraint to add.
        :return: None
        :raises: InvalidConstraintException if the constraint is not valid
        """
        is_valid, message = constraint.is_valid(self._constraints)
        if is_valid:
            self._constraints.append(constraint)
        else:
            raise InvalidConstraintException(f"Invalid constraint: {message}")

    def clear_constraints(self) -> None:
        """
        Clears the current lineup optimizer constraints.

        :return: None
        """
        self._constraints = []

    def optimize_lineup(self) -> OptimizedLineup:
        """
        Generates and returns an optimized lineup for a given fantasy football site.
        The lineup is generated using the class's data variable and is optimized under provided constraints.

        :return: The optimized lineup.
        :raises: ValueError, InvalidDataFrameException
        """
        position_constraints = self.position_constraints()
        if not data_frame_utils.col_contains_all_values(self._data, self.position_col, position_constraints.keys()):
            raise InvalidDataFrameException('Data frame is missing required positions')
        self._data['LpVariable'] = self._data.apply(lambda x: LpVariable(f"{x[self._position_col]}_{x.name}",
                                                                         cat='Binary'), axis=1)
        problem = LpProblem(f"{self.site_name()}LineupOptimization", LpMaximize)
        for k, v in position_constraints.items():
            players = self._data[self._data[self._position_col] == k]
            problem += lpSum(players['LpVariable']) >= v[0]
            problem += lpSum(players['LpVariable']) <= v[1]
        problem += lpSum(self._data[self._points_col] * self._data['LpVariable'])
        problem += constraints.LineupSizeConstraint(self.num_players()).apply(self._data)[0]
        problem += constraints.MaxSalaryCapConstraint(self.salary_cap(), self._salary_col).apply(self._data)[0]
        for constraint in self._constraints:
            for c in constraint.apply(self._data):  # stack-related constraints may return multiple from apply()
                problem += c
        problem.solve(PULP_CBC_CMD(msg=False))
        if not pulp_utils.is_optimal_solution_found(problem):
            raise UnsolvableLineupException('No optimal solution found under current lineup constraints')
        return OptimizedLineup(self, self.site_name())
