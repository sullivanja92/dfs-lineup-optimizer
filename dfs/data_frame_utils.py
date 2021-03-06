from typing import Any, Dict, Iterable, Sequence

import pandas as pd


def contains_all_columns(df: pd.DataFrame, columns: Sequence[str]) -> bool:
    """
    This function checks whether or not a DataFrame contains all columns in a provided sequence.

    :param df: The DataFrame to check.
    :param columns: The sequence of columns to check for.
    :return: A bool indicating whether or not the dataframe contains all of the columns.
    :raises: A ValueError if the provided arguments are invalid.
    """
    if df is None or columns is None:
        raise ValueError("Data frame and columns must not be None")
    return all([x in df.columns for x in columns])


def col_contains_all_values(df: pd.DataFrame, col: str, values: Iterable[Any]) -> bool:
    """
    This function checks whether or not a DataFrame's column contains all values in a provided iterable.

    :param df: The DataFrame to check.
    :param col: The DataFrame column name.
    :param values: The values to check for.
    :return: A bool indicating whether or not all values are found in the column.
    :raises: A ValueError if the arguments are invalid.
    """
    if col is None or col not in df.columns:
        raise ValueError(f"The column, {col}, must be found in data frame")
    return all(val in df[col].unique() for val in values)


def map_index_to_col(df: pd.DataFrame, col: str) -> Dict[Any, Any]:
    """
    This function creates and returns a dict by mapping a DataFrame's index to a column's values.

    :param df: The DataFrame to construct the dict from.
    :param col: The column name to use for dict values.
    :return: A dict created with DataFrame index and column values
    :raises: A ValueError if the arguments are invalid
    """
    if df is None:
        raise ValueError('Data frame cannot be None')
    if col is None or col not in df.columns:
        raise ValueError('The column must not be None and must be found in the data frame')
    return {i: df.loc[i][col] for i in df.index}


def merge_dicts(*args: Dict) -> Dict[Any, Any]:
    """
    This function merges the provided dicts into a single dict.

    :param args: The dicts to merge.
    :return: The merged dict.
    :raises: A ValueError if no non-none dicts are passed.
    """
    dicts = list(filter(lambda x: x is not None, args))
    if len(dicts) == 0:
        raise ValueError('No valid dicts to merge')
    return {k: v for d in dicts for k, v in d.items()}


def map_cols_and_filter_by_values(df: pd.DataFrame, column_mapping: Dict[str, str]) -> pd.DataFrame:
    """
    Renames a data frame's columns and returns the data frame filtered by updated column names.

    :param df: The data frame.
    :param column_mapping: The mapping of column names to update.
    :return: The filtered data frame.
    :raises: ValueError if either argument is None.
    """
    if df is None or column_mapping is None:
        raise ValueError('Data frame and column mapping arguments must not be none')
    df.rename(columns=column_mapping, inplace=True)
    return df[column_mapping.values()]
