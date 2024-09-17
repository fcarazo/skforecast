################################################################################
#                            ForecasterAutoreg                                 #
#                                                                              #
# This work by skforecast team is licensed under the BSD 3-Clause License.     #
################################################################################
# coding=utf-8

from typing import Union, Tuple, Optional, Callable
import warnings
import logging
import sys
import numpy as np
import pandas as pd
import inspect
import textwrap
from sklearn.exceptions import NotFittedError
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import KBinsDiscretizer
from sklearn.base import clone
from copy import copy

import skforecast
from ..ForecasterBase import ForecasterBase
from ..utils import initialize_lags
from ..utils import initialize_weights
from ..utils import check_select_fit_kwargs
from ..utils import check_y
from ..utils import check_exog
from ..utils import get_exog_dtypes
from ..utils import check_exog_dtypes
from ..utils import check_predict_input
from ..utils import check_interval
from ..utils import preprocess_y
from ..utils import preprocess_last_window
from ..utils import preprocess_exog
from ..utils import input_to_frame
from ..utils import expand_index
from ..utils import transform_numpy
from ..utils import transform_series
from ..utils import transform_dataframe
from ..preprocessing import TimeSeriesDifferentiator

logging.basicConfig(
    format = '%(name)-10s %(levelname)-5s %(message)s', 
    level  = logging.INFO,
)


class ForecasterAutoreg(ForecasterBase):
    """
    This class turns any regressor compatible with the scikit-learn API into a
    recursive autoregressive (multi-step) forecaster.
    
    Parameters
    ----------
    regressor : regressor or pipeline compatible with the scikit-learn API
        An instance of a regressor or pipeline compatible with the scikit-learn API
    lags : int, list, numpy ndarray, range
        Lags used as predictors. Index starts at 1, so lag 1 is equal to t-1. 
    
        - `int`: include lags from 1 to `lags` (included).
        - `list`, `1d numpy ndarray` or `range`: include only lags present in 
        `lags`, all elements must be int.
    transformer_y : object transformer (preprocessor), default `None`
        An instance of a transformer (preprocessor) compatible with the scikit-learn
        preprocessing API with methods: fit, transform, fit_transform and inverse_transform.
        ColumnTransformers are not allowed since they do not have inverse_transform method.
        The transformation is applied to `y` before training the forecaster. 
    transformer_exog : object transformer (preprocessor), default `None`
        An instance of a transformer (preprocessor) compatible with the scikit-learn
        preprocessing API. The transformation is applied to `exog` before training the
        forecaster. `inverse_transform` is not available when using ColumnTransformers.
    weight_func : Callable, default `None`
        Function that defines the individual weights for each sample based on the
        index. For example, a function that assigns a lower weight to certain dates.
        Ignored if `regressor` does not have the argument `sample_weight` in its `fit`
        method. The resulting `sample_weight` cannot have negative values.
    differentiation : int, default `None`
        Order of differencing applied to the time series before training the forecaster.
        If `None`, no differencing is applied. The order of differentiation is the number
        of times the differencing operation is applied to a time series. Differencing
        involves computing the differences between consecutive data points in the series.
        Differentiation is reversed in the output of `predict()` and `predict_interval()`.
        **WARNING: This argument is newly introduced and requires special attention. It
        is still experimental and may undergo changes.**
        **New in version 0.10.0**
    fit_kwargs : dict, default `None`
        Additional arguments to be passed to the `fit` method of the regressor.
    binner_kwargs : dict, default `None`
        Additional arguments to pass to the `KBinsDiscretizer` used to discretize 
        the residuals into k bins according to the predicted values associated 
        with each residual. The `encode' argument is always set to 'ordinal' 
        and `dtype' to np.float64.
        **New in version 0.12.0**
    forecaster_id : str, int, default `None`
        Name used as an identifier of the forecaster.
    
    Attributes
    ----------
    regressor : regressor or pipeline compatible with the scikit-learn API
        An instance of a regressor or pipeline compatible with the scikit-learn API.
    lags : numpy ndarray
        Lags used as predictors.
    transformer_y : object transformer (preprocessor)
        An instance of a transformer (preprocessor) compatible with the scikit-learn
        preprocessing API with methods: fit, transform, fit_transform and inverse_transform.
        ColumnTransformers are not allowed since they do not have inverse_transform method.
        The transformation is applied to `y` before training the forecaster.
    transformer_exog : object transformer (preprocessor)
        An instance of a transformer (preprocessor) compatible with the scikit-learn
        preprocessing API. The transformation is applied to `exog` before training the
        forecaster. `inverse_transform` is not available when using ColumnTransformers.
    weight_func : Callable
        Function that defines the individual weights for each sample based on the
        index. For example, a function that assigns a lower weight to certain dates.
        Ignored if `regressor` does not have the argument `sample_weight` in its `fit`
        method. The resulting `sample_weight` cannot have negative values.
    differentiation : int, default `None`
        Order of differencing applied to the time series before training the forecaster.
        If `None`, no differencing is applied. The order of differentiation is the number
        of times the differencing operation is applied to a time series. Differencing
        involves computing the differences between consecutive data points in the series.
        Differentiation is reversed in the output of `predict()` and `predict_interval()`.
        **WARNING: This argument is newly introduced and requires special attention. It
        is still experimental and may undergo changes.**
        **New in version 0.10.0**
    binner : sklearn.preprocessing.KBinsDiscretizer
        `KBinsDiscretizer` used to discretize residuals into k bins according 
        to the predicted values associated with each residual.
        **New in version 0.12.0**
    binner_kwargs : dict
        Additional arguments to pass to the `KBinsDiscretizer` used to discretize 
        the residuals into k bins according to the predicted values associated 
        with each residual. The `encode' argument is always set to 'ordinal' 
        and `dtype' to np.float64.
        **New in version 0.12.0**
    source_code_weight_func : str
        Source code of the custom function used to create weights.
    differentiation : int
        Order of differencing applied to the time series before training the 
        forecaster.
    differentiator : TimeSeriesDifferentiator
        Skforecast object used to differentiate the time series.
    max_lag : int
        Maximum value of lag included in `lags`.
    window_size : int
        Size of the window needed to create the predictors. It is equal to `max_lag`.
    window_size_diff : int
        Size of the window extended by the order of differentiation. When using
        differentiation, the `window_size` is increased by the order of differentiation
        so that the predictors can be created correctly.
    last_window_ : pandas DataFrame
        This window represents the most recent data observed by the predictor
        during its training phase. It contains the values needed to predict the
        next step immediately after the training data. These values are stored
        in the original scale of the time series before undergoing any transformations
        or differentiation. When `differentiation` parameter is specified, the
        dimensions of the `last_window_` are expanded as many values as the order
        of differentiation. For example, if `lags` = 7 and `differentiation` = 1,
        `last_window_` will have 8 values.
    index_type_ : type
        Type of index of the input used in training.
    index_freq_ : str
        Frequency of Index of the input used in training.
    training_range_ : pandas Index
        First and last values of index of the data used during training.
    exog_in_ : bool
        If the forecaster has been trained using exogenous variable/s.
    exog_names_in_ : list
        Names of the exogenous variables used during training.
    exog_type_in_ : type
        Type of exogenous data (pandas Series or DataFrame) used in training.
    exog_dtypes_in_ : dict
        Type of each exogenous variable/s used in training. If `transformer_exog` 
        is used, the dtypes are calculated after the transformation.
    X_train_exog_names_out_ : list
        Names of the exogenous variables included in the matrix `X_train` created
        internally for training. It can be different from `exog_names_in_` if
        some exogenous variables are transformed during the training process.
    X_train_features_names_out_ : list
        Names of columns of the matrix created internally for training.
    fit_kwargs : dict
        Additional arguments to be passed to the `fit` method of the regressor.
    in_sample_residuals_ : numpy ndarray
        Residuals of the model when predicting training data. Only stored up to
        1000 values. If `transformer_y` is not `None`, residuals are stored in the
        transformed scale.
    in_sample_residuals_by_bin_ : dict
        In sample residuals binned according to the predicted value each residual
        is associated with. Only stored up to 200 values per bin. If `transformer_y`
        is not `None`, residuals are stored in the transformed scale.
        **New in version 0.12.0**
    out_sample_residuals_ : numpy ndarray
        Residuals of the model when predicting non training data. Only stored
        up to 1000 values. If `transformer_y` is not `None`, residuals
        are assumed to be in the transformed scale. Use `set_out_sample_residuals` 
        method to set values.
    out_sample_residuals_by_bin_ : dict
        Out of sample residuals binned according to the predicted value each residual
        is associated with. Only stored up to 200 values per bin. If `transformer_y`
        is not `None`, residuals are assumed to be in the transformed scale.
        **New in version 0.12.0**
    creation_date : str
        Date of creation.
    is_fitted : bool
        Tag to identify if the regressor has been fitted (trained).
    fit_date : str
        Date of last fit.
    skforecast_version : str
        Version of skforecast library used to create the forecaster.
    python_version : str
        Version of python used to create the forecaster.
    forecaster_id : str, int
        Name used as an identifier of the forecaster.
    
    """

    def __init__(
        self,
        regressor: object,
        lags: Union[int, np.ndarray, list],
        transformer_y: Optional[object] = None,
        transformer_exog: Optional[object] = None,
        weight_func: Optional[Callable] = None,
        differentiation: Optional[int] = None,
        fit_kwargs: Optional[dict] = None,
        binner_kwargs: Optional[dict] = None,
        forecaster_id: Optional[Union[str, int]] = None
    ) -> None:
        
        self.regressor                    = copy(regressor)
        self.transformer_y                = transformer_y
        self.transformer_exog             = transformer_exog
        self.weight_func                  = weight_func
        self.source_code_weight_func      = None
        self.differentiation              = differentiation
        self.differentiator               = None
        self.last_window_                 = None
        self.index_type_                  = None
        self.index_freq_                  = None
        self.training_range_              = None
        self.exog_in_                     = False
        self.exog_names_in_               = None
        self.exog_type_in_                = None
        self.exog_dtypes_in_              = None
        self.X_train_exog_names_out_      = None
        self.X_train_features_names_out_  = None
        self.in_sample_residuals_         = None
        self.out_sample_residuals_        = None
        self.in_sample_residuals_by_bin_  = None
        self.out_sample_residuals_by_bin_ = None
        self.creation_date                = pd.Timestamp.today().strftime('%Y-%m-%d %H:%M:%S')
        self.is_fitted                    = False
        self.fit_date                     = None
        self.skforecast_version           = skforecast.__version__
        self.python_version               = sys.version.split(" ")[0]
        self.forecaster_id                = forecaster_id

        self.lags = initialize_lags(type(self).__name__, lags)
        self.max_lag = max(self.lags)
        self.window_size = self.max_lag
        self.window_size_diff = self.max_lag

        self.binner_kwargs = binner_kwargs
        if binner_kwargs is None:
            self.binner_kwargs = {
                'n_bins': 10, 'encode': 'ordinal', 'strategy': 'quantile',
                'subsample': 10000, 'random_state': 789654, 'dtype': np.float64
            }
        else:
            self.binner_kwargs = binner_kwargs
            self.binner_kwargs['encode'] = 'ordinal'
            self.binner_kwargs['dtype'] = np.float64
        self.binner = KBinsDiscretizer(**self.binner_kwargs)
        self.binner_intervals = None

        if self.differentiation is not None:
            if not isinstance(differentiation, int) or differentiation < 1:
                raise ValueError(
                    (f"Argument `differentiation` must be an integer equal to or "
                     f"greater than 1. Got {differentiation}.")
                )
            self.window_size_diff += self.differentiation
            self.differentiator = TimeSeriesDifferentiator(order=self.differentiation)

        self.weight_func, self.source_code_weight_func, _ = initialize_weights(
            forecaster_name = type(self).__name__, 
            regressor       = regressor, 
            weight_func     = weight_func, 
            series_weights  = None
        )

        self.fit_kwargs = check_select_fit_kwargs(
                              regressor  = regressor,
                              fit_kwargs = fit_kwargs
                          )


    def __repr__(
        self
    ) -> str:
        """
        Information displayed when a ForecasterAutoreg object is printed.
        """

        if isinstance(self.regressor, Pipeline):
            name_pipe_steps = tuple(name + "__" for name in self.regressor.named_steps.keys())
            params = {key: value for key, value in self.regressor.get_params().items()
                      if key.startswith(name_pipe_steps)}
        else:
            params = self.regressor.get_params(deep=True)
        params = "\n    " + textwrap.fill(str(params), width=80, subsequent_indent="    ")

        exog_names_in_ = None
        if self.exog_names_in_ is not None:
            exog_names_in_ = copy(self.exog_names_in_)
            if len(exog_names_in_) > 50:
                exog_names_in_ = exog_names_in_[:50] + ["..."]
            exog_names_in_ = ", ".join(exog_names_in_)
            if len(exog_names_in_) > 58:
                exog_names_in_ = "\n    " + textwrap.fill(
                    exog_names_in_, width=80, subsequent_indent="    "
                )

        info = (
            f"{'=' * len(type(self).__name__)} \n"
            f"{type(self).__name__} \n"
            f"{'=' * len(type(self).__name__)} \n"
            f"Regressor: {self.regressor} \n"
            f"Lags: {self.lags} \n"
            f"Window size: {self.window_size} \n"
            f"Exogenous included: {self.exog_in_} \n"
            f"Exogenous names: {exog_names_in_} \n"
            f"Transformer for y: {self.transformer_y} \n"
            f"Transformer for exog: {self.transformer_exog} \n"
            f"Weight function included: {True if self.weight_func is not None else False} \n"
            f"Differentiation order: {self.differentiation} \n"
            f"Training range: {self.training_range_.to_list() if self.is_fitted else None} \n"
            f"Training index type: {str(self.index_type_).split('.')[-1][:-2] if self.is_fitted else None} \n"
            f"Training index frequency: {self.index_freq_ if self.is_fitted else None} \n"
            f"Regressor parameters: {params} \n"
            f"fit_kwargs: {self.fit_kwargs} \n"
            f"Creation date: {self.creation_date} \n"
            f"Last fit date: {self.fit_date} \n"
            f"Skforecast version: {self.skforecast_version} \n"
            f"Python version: {self.python_version} \n"
            f"Forecaster id: {self.forecaster_id} \n"
        )

        return info


    def _repr_html_(self):
        """
        HTML representation of the object.
        The "General Information" section is expanded by default.
        """
        
        if isinstance(self.regressor, Pipeline):
            name_pipe_steps = tuple(name + "__" for name in self.regressor.named_steps.keys())
            params = {key: value for key, value in self.regressor.get_params().items()
                    if key.startswith(name_pipe_steps)}
        else:
            params = self.regressor.get_params(deep=True)
        params = str(params)

        style = """
        <style>
            .container {
                font-family: 'Arial', sans-serif;
                font-size: 0.9em;
                color: #333;
                border: 1px solid #ddd;
                background-color: #fafafa;
                padding: 5px 15px;
                border-radius: 8px;
                max-width: 600px;
                #margin: auto;
            }
            .container h2 {
                font-size: 1.2em;
                color: #222;
                border-bottom: 2px solid #ddd;
                padding-bottom: 5px;
                margin-bottom: 15px;
            }
            .container details {
                margin: 10px 0;
            }
            .container summary {
                font-weight: bold;
                font-size: 1.1em;
                cursor: pointer;
                margin-bottom: 5px;
                background-color: #f0f0f0;
                padding: 5px;
                border-radius: 5px;
            }
            .container summary:hover {
            background-color: #e0e0e0;
            }
            .container ul {
                font-family: 'Courier New', monospace;
                list-style-type: none;
                padding-left: 20px;
                margin: 10px 0;
            }
            .container li {
                margin: 5px 0;
                font-family: 'Courier New', monospace;
            }
            .container li strong {
                font-weight: bold;
                color: #444;
            }
            .container li::before {
                content: "- ";
                color: #666;
            }
        </style>
        """
        
        content = f"""
        <div class="container">
            <h2>{type(self).__name__}</h2>
            <details open>
                <summary>General Information</summary>
                <ul>
                    <li><strong>Regressor:</strong> {self.regressor}</li>
                    <li><strong>Lags:</strong> {self.lags}</li>
                    <li><strong>Window size:</strong> {self.window_size}</li>
                    <li><strong>Exogenous included:</strong> {self.exog_in_}</li>
                    <li><strong>Weight function included:</strong> {self.weight_func is not None}</li>
                    <li><strong>Differentiation order:</strong> {self.differentiation}</li>
                    <li><strong>Creation date:</strong> {self.creation_date}</li>
                    <li><strong>Last fit date:</strong> {self.fit_date}</li>
                    <li><strong>Skforecast version:</strong> {self.skforecast_version}</li>
                    <li><strong>Python version:</strong> {self.python_version}</li>
                    <li><strong>Forecaster id:</strong> {self.forecaster_id}</li>
                </ul>
            </details>
            <details>
                <summary>Exogenous Variables</summary>
                <ul>
                     {self.exog_names_in_}
                </ul>
            </details>
            <details>
                <summary>Data Transformations</summary>
                <ul>
                    <li><strong>Transformer for y:</strong> {self.transformer_y}</li>
                    <li><strong>Transformer for exog:</strong> {self.transformer_exog}</li>
                </ul>
            </details>
            <details>
                <summary>Training Information</summary>
                <ul>
                    <li><strong>Training range:</strong> {self.training_range_.to_list() if self.is_fitted else 'Not fitted'}</li>
                    <li><strong>Training index type:</strong> {str(self.index_type_).split('.')[-1][:-2] if self.is_fitted else 'Not fitted'}</li>
                    <li><strong>Training index frequency:</strong> {self.index_freq_ if self.is_fitted else 'Not fitted'}</li>
                </ul>
            </details>
            <details>
                <summary>Regressor Parameters</summary>
                <ul>
                    {params}
                </ul>
            </details>
            <details>
                <summary>Fit Kwargs</summary>
                <ul>
                    {self.fit_kwargs}
                </ul>
            </details>
            <p>
                <a href="https://skforecast.org/{skforecast.__version__}/api/forecasterautoreg#forecasterautoreg.html">&#128712 <strong>API Reference</strong></a>
                &nbsp;&nbsp;
                <a href="https://skforecast.org/{skforecast.__version__}/user_guides/autoregresive-forecaster.html">&#128462 <strong>User Guide</strong></a>
            </p>
        </div>
        """

        # Return the combined style and content
        return style + content


    def _create_lags(
        self, 
        y: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Transforms a 1d array into a 2d array (X) and a 1d array (y). Each row
        in X is associated with a value of y and it represents the lags that
        precede it.
        
        Notice that, the returned matrix X_data, contains the lag 1 in the first
        column, the lag 2 in the second column and so on.
        
        Parameters
        ----------
        y : numpy ndarray
            1d numpy ndarray Training time series.

        Returns
        -------
        X_data : numpy ndarray
            2d numpy ndarray with the lagged values (predictors). 
            Shape: (samples - max(self.lags), len(self.lags))
        y_data : numpy ndarray
            1d numpy ndarray with the values of the time series related to each 
            row of `X_data`. 
            Shape: (samples - max(self.lags), )
        
        """

        n_splits = len(y) - self.max_lag
        if n_splits <= 0:
            raise ValueError(
                (f"The maximum lag ({self.max_lag}) must be less than the length "
                 f"of the series ({len(y)}).")
            )

        X_data = np.full(shape=(n_splits, len(self.lags)), fill_value=np.nan, dtype=float)

        for i, lag in enumerate(self.lags):
            X_data[:, i] = y[self.max_lag - lag: -lag]

        y_data = y[self.max_lag:]

        return X_data, y_data


    def create_train_X_y(
        self,
        y: pd.Series,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None
    ) -> Tuple[pd.DataFrame, pd.Series]:
        """
        Create training matrices from univariate time series and exogenous
        variables.
        
        Parameters
        ----------
        y : pandas Series
            Training time series.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s. Must have the same
            number of observations as `y` and their indexes must be aligned.

        Returns
        -------
        X_train : pandas DataFrame
            Training values (predictors).
            Shape: (len(y) - self.max_lag, len(self.lags))
        y_train : pandas Series
            Values (target) of the time series related to each row of `X_train`.
            Shape: (len(y) - self.max_lag, )
        
        """

        check_y(y=y)
        y = input_to_frame(data=y, input_name='y')

        fit_transformer = False if self.is_fitted else True
        y = transform_dataframe(
                df                = y, 
                transformer       = self.transformer_y,
                fit               = fit_transformer,
                inverse_transform = False,
            )
        y_values, y_index = preprocess_y(y=y)

        if self.differentiation is not None:
            if not self.is_fitted:
                y_values = self.differentiator.fit_transform(y_values)
            else:
                differentiator = clone(self.differentiator)
                y_values = differentiator.fit_transform(y_values)

        if exog is not None:
            check_exog(exog=exog, allow_nan=True)
            exog = input_to_frame(data=exog, input_name='exog')
            if len(exog) != len(y):
                raise ValueError(
                    (f"`exog` must have same number of samples as `y`. "
                     f"length `exog`: ({len(exog)}), length `y`: ({len(y)})")
                )

            exog = transform_dataframe(
                       df                = exog,
                       transformer       = self.transformer_exog,
                       fit               = fit_transformer,
                       inverse_transform = False
                   )

            check_exog_dtypes(exog, call_check_exog=True)

            _, exog_index = preprocess_exog(exog=exog, return_values=False)
            if not (exog_index[:len(y_index)] == y_index).all():
                raise ValueError(
                    ("Different index for `y` and `exog`. They must be equal "
                     "to ensure the correct alignment of values.")
                )

        X_train, y_train = self._create_lags(y=y_values)
        X_train_features_names_out_ = [f"lag_{i}" for i in self.lags]
        X_train = pd.DataFrame(
                      data    = X_train,
                      columns = X_train_features_names_out_,
                      index   = y_index[self.max_lag:]
                  )

        if exog is not None:
            # The first `self.max_lag` positions have to be removed from exog
            # since they are not in X_train.
            exog_to_train = exog.iloc[self.max_lag:, ]
            exog_to_train.index = exog_index[self.max_lag:]
            X_train = pd.concat((X_train, exog_to_train), axis=1)
            # TODO: move self to fit method and make X_train_features_names_out_ a return
            if not self.is_fitted:
                self.X_train_exog_names_out_ = exog_to_train.columns.to_list()

        # TODO: move self to fit method and make X_train_features_names_out_ a return
        if not self.is_fitted:
            self.X_train_features_names_out_ = X_train.columns.to_list()

        # TODO: DataFrame or Series?
        y_train = pd.Series(
                      data  = y_train,
                      index = y_index[self.max_lag:],
                      name  = 'y'
                  )

        if self.differentiation is not None:
            X_train = X_train.iloc[self.differentiation:]
            y_train = y_train.iloc[self.differentiation:]

        return X_train, y_train


    def create_sample_weights(
        self,
        X_train: pd.DataFrame,
    ) -> np.ndarray:
        """
        Crate weights for each observation according to the forecaster's attribute
        `weight_func`.

        Parameters
        ----------
        X_train : pandas DataFrame
            Dataframe created with the `create_train_X_y` method, first return.

        Returns
        -------
        sample_weight : numpy ndarray
            Weights to use in `fit` method.

        """

        sample_weight = None

        if self.weight_func is not None:
            sample_weight = self.weight_func(X_train.index)

        if sample_weight is not None:
            if np.isnan(sample_weight).any():
                raise ValueError(
                    "The resulting `sample_weight` cannot have NaN values."
                )
            if np.any(sample_weight < 0):
                raise ValueError(
                    "The resulting `sample_weight` cannot have negative values."
                )
            if np.sum(sample_weight) == 0:
                raise ValueError(
                    ("The resulting `sample_weight` cannot be normalized because "
                     "the sum of the weights is zero.")
                )

        return sample_weight


    def fit(
        self,
        y: pd.Series,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None,
        store_last_window: bool = True,
        store_in_sample_residuals: bool = True
    ) -> None:
        """
        Training Forecaster.

        Additional arguments to be passed to the `fit` method of the regressor 
        can be added with the `fit_kwargs` argument when initializing the forecaster.
        
        Parameters
        ----------
        y : pandas Series
            Training time series.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s. Must have the same
            number of observations as `y` and their indexes must be aligned so
            that y[i] is regressed on exog[i].
        store_last_window : bool, default `True`
            Whether or not to store the last window (`last_window_`) of training data.
        store_in_sample_residuals : bool, default `True`
            If `True`, in-sample residuals will be stored in the forecaster object
            after fitting (`in_sample_residuals_` attribute).

        Returns
        -------
        None
        
        """

        # Reset values in case the forecaster has already been fitted.
        self.last_window_                = None
        self.index_type_                 = None
        self.index_freq_                 = None
        self.training_range_             = None
        self.exog_in_                    = False
        self.exog_names_in_              = None
        self.exog_type_in_               = None
        self.exog_dtypes_in_             = None
        self.X_train_exog_names_out_     = None
        self.X_train_features_names_out_ = None
        self.in_sample_residuals_        = None
        self.is_fitted                   = False
        self.fit_date                    = None

        X_train, y_train = self.create_train_X_y(y=y, exog=exog)
        sample_weight = self.create_sample_weights(X_train=X_train)

        if sample_weight is not None:
            self.regressor.fit(
                X             = X_train,
                y             = y_train,
                sample_weight = sample_weight,
                **self.fit_kwargs
            )
        else:
            self.regressor.fit(X=X_train, y=y_train, **self.fit_kwargs)

        self.is_fitted = True
        self.fit_date = pd.Timestamp.today().strftime('%Y-%m-%d %H:%M:%S')
        self.training_range_ = preprocess_y(y=y, return_values=False)[1][[0, -1]]
        self.index_type_ = type(X_train.index)
        if isinstance(X_train.index, pd.DatetimeIndex):
            self.index_freq_ = X_train.index.freqstr
        else: 
            self.index_freq_ = X_train.index.step

        if exog is not None:
            self.exog_in_ = True
            self.exog_type_in_ = type(exog)
            self.exog_dtypes_in_ = get_exog_dtypes(exog=exog)
            self.exog_names_in_ = (
                exog.columns.to_list()
                if isinstance(exog, pd.DataFrame)
                else [exog.name]
            )

        # This is done to save time during fit in functions such as backtesting()
        if store_in_sample_residuals:
            in_sample_predictions = pd.Series(
                                        data  = self.regressor.predict(X_train),
                                        index = X_train.index
                                    )
            self._binning_in_sample_residuals(
                y_true = y_train,
                y_pred = in_sample_predictions
            )

        # The last time window of training data is stored so that lags needed as
        # predictors in the first iteration of `predict()` can be calculated. It
        # also includes the values need to calculate the diferenctiation.
        if store_last_window:
            self.last_window_ = (
                y.iloc[-self.window_size_diff:]
                .copy()
                .to_frame(name=y.name if y.name is not None else 'y')
            )


    def _binning_in_sample_residuals(
        self,
        y_true: pd.Series,
        y_pred: pd.Series,
        random_state: int = 95123
    ) -> None:
        """
        Binning residuals according to the predicted value each residual is
        associated with. First a sklearn.preprocessing.KBinsDiscretizer
        is fitted to the predicted values. Then, residuals are binned according
        to the predicted value each residual is associated with. Residuals are
        stored in the forecaster object as `in_sample_residuals_` and
        `in_sample_residuals_by_bin_`. Only up to 200 residuals are stored per bin.

        Parameters
        ----------
        y_true : pandas Series
            True values of the time series.
        y_pred : pandas Series
            Predicted values of the time series.  
        random_state : int, default `95123`
            Set a seed for the random generator so that the stored sample 
            residuals are always deterministic.

        Returns
        -------
        None
        
        """

        y_pred = y_pred.rename('prediction')
        residuals = (y_true - y_pred).rename('residual')
        data = pd.merge(
                   residuals,
                   y_pred,
                   left_index  = True,
                   right_index = True
               )
        self.binner.fit(data[['prediction']].to_numpy())
        data['bin'] = self.binner.transform(data[['prediction']].to_numpy()).astype(int)
        self.in_sample_residuals_by_bin_ = (
            data.groupby('bin')['residual'].apply(np.array).to_dict()
        )

        # Only up to 200 residuals are stored per bin
        for k, v in self.in_sample_residuals_by_bin_.items():
            # TODO: Include `random_state` in fit method to allow the user
            # change the residual sample stored.
            rng = np.random.default_rng(seed=random_state)
            if len(v) > 200:
                sample = rng.choice(a=v, size=200, replace=False)
                self.in_sample_residuals_by_bin_[k] = sample

        self.in_sample_residuals_ = np.concatenate(list(
            self.in_sample_residuals_by_bin_.values()
        ))

        self.binner_intervals = {
            i: (
                self.binner.bin_edges_[0][i],
                (
                    self.binner.bin_edges_[0][i + 1]
                    if i + 1 < len(self.binner.bin_edges_[0])
                    else None
                ),
            )
            for i in range(len(self.binner.bin_edges_[0]) - 1)
        }


    def _create_predict_inputs(
        self,
        steps: int,
        last_window: Optional[Union[pd.Series, pd.DataFrame]] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None,
        predict_boot: bool = False,
        in_sample_residuals: bool = True,
        binned_residuals: bool = False,
        check_inputs: bool = True
    ) -> Tuple[np.ndarray, np.ndarray, pd.Index]:
        """
        Create inputs needed for the first iteration of the prediction process. 
        Since it is a recursive process, last window is updated at each 
        iteration of the prediction process.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, pandas DataFrame, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in `self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.
        predict_boot : bool, default `False`
            If `True`, residuals are returned to generate bootstrapping predictions.
        in_sample_residuals : bool, default `True`
            If `True`, residuals from the training data are used as proxy of
            prediction error to create predictions. If `False`, out of sample 
            residuals are used. In the latter case, the user should have
            calculated and stored the residuals within the forecaster (see
            `set_out_sample_residuals()`).
        binned_residuals : bool, default `False`
            If `True`, residuals used in each bootstrapping iteration are selected
            conditioning on the predicted values. If `False`, residuals are selected
            randomly without conditioning on the predicted values.
            **WARNING: This argument is newly introduced and requires special attention.
            It is still experimental and may undergo changes.
            **New in version 0.12.0**
        check_inputs : bool, default `True`
            If `True`, the input is checked for possible warnings and errors 
            with the `check_predict_input` function. This argument is created 
            for internal use and is not recommended to be changed.

        Returns
        -------
        last_window_values : numpy ndarray
            Series predictors.
        exog_values : numpy ndarray, default `None`
            Exogenous variable/s included as predictor/s.
        prediction_index : pandas Index
            Index of the predictions.
        
        """

        if last_window is None:
            last_window = self.last_window_

        if check_inputs:
            check_predict_input(
                forecaster_name  = type(self).__name__,
                steps            = steps,
                is_fitted        = self.is_fitted,
                exog_in_         = self.exog_in_,
                index_type_      = self.index_type_,
                index_freq_      = self.index_freq_,
                window_size      = self.window_size_diff,
                last_window      = last_window,
                last_window_exog = None,
                exog             = exog,
                exog_type_in_    = self.exog_type_in_,
                exog_names_in_   = self.exog_names_in_,
                interval         = None,
                max_steps        = None
            )
        
            if predict_boot and not in_sample_residuals:
                if not binned_residuals and self.out_sample_residuals_ is None:
                    raise ValueError(
                        ("`forecaster.out_sample_residuals_` is `None`. Use "
                         "`in_sample_residuals=True` or the `set_out_sample_residuals()` "
                         "method before predicting.")
                    )
                if binned_residuals and self.out_sample_residuals_by_bin_ is None:
                    raise ValueError(
                        ("`forecaster.out_sample_residuals_by_bin_` is `None`. Use "
                         "`in_sample_residuals=True` or the `set_out_sample_residuals()` "
                         "method before predicting.")
                    )

        last_window = last_window.iloc[-self.window_size_diff:].copy()
        last_window_values, last_window_index = preprocess_last_window(
                                                    last_window = last_window
                                                )

        last_window_values = transform_numpy(
                                 array             = last_window_values,
                                 transformer       = self.transformer_y,
                                 fit               = False,
                                 inverse_transform = False
                             )
        if self.differentiation is not None:
            last_window_values = self.differentiator.fit_transform(last_window_values)

        if exog is not None:
            exog = input_to_frame(data=exog, input_name='exog')
            exog = exog.loc[:, self.exog_names_in_]
            exog = transform_dataframe(
                       df                = exog,
                       transformer       = self.transformer_exog,
                       fit               = False,
                       inverse_transform = False
                   )
            check_exog_dtypes(exog=exog)
            exog_values = exog.to_numpy()[:steps]
        else:
            exog_values = None

        prediction_index = expand_index(
                               index = last_window_index,
                               steps = steps
                           )

        return last_window_values, exog_values, prediction_index


    def _recursive_predict(
        self,
        steps: int,
        last_window: np.ndarray,
        exog: Optional[np.ndarray] = None,
        residuals: Optional[np.ndarray] = None,
        binned_residuals: bool = False,
        rng: Optional[np.random.Generator] = None
    ) -> np.ndarray:
        """
        Predict n steps ahead. It is an iterative process in which, each prediction,
        is used as a predictor for the next step.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : numpy ndarray
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
        exog : numpy ndarray, default `None`
            Exogenous variable/s included as predictor/s.
        residuals : numpy ndarray, default `None`
            Residuals used to generate bootstrapping predictions.
        binned_residuals : bool, default `False`
            If `True`, residuals used in each bootstrapping iteration are selected
            conditioning on the predicted values. If `False`, residuals are selected
            randomly without conditioning on the predicted values.
            **WARNING: This argument is newly introduced and requires special attention.
            It is still experimental and may undergo changes.
            **New in version 0.12.0**
        rng : numpy Generator, default `None`
            Random number generator used to select residuals in bootstrapping
            predictions. 

        Returns
        -------
        predictions : numpy ndarray
            Predicted values.
        
        """

        predictions = np.full(shape=steps, fill_value=np.nan)
        last_window = np.concatenate((last_window, predictions))

        for i in range(steps):

            X = last_window[-self.lags - (steps - i)]
            if exog is not None:
                X = np.concatenate((X, exog[i, ]))
        
            with warnings.catch_warnings():
                # Suppress scikit-learn warning: "X does not have valid feature names,
                # but NoOpTransformer was fitted with feature names".
                warnings.filterwarnings(
                    "ignore", 
                    message="X does not have valid feature names", 
                    category=UserWarning
                )
                # TODO: Check with RandomForestRegressor
                pred = self.regressor.predict(X.reshape(1, -1)).ravel()
            
            if residuals is not None:

                if binned_residuals:
                    predicted_bin = (
                        int(self.binner.transform(pred.reshape(1, -1))[0, 0])
                    )
                    step_residual = rng.choice(a=residuals[predicted_bin], size=1)
                else:
                    step_residual = residuals[i]
                
                pred += step_residual
            
            predictions[i] = pred

            # Update `last_window` values. The first position is discarded and 
            # the new prediction is added at the end.
            last_window[-(steps - i)] = pred

        return predictions


    def create_predict_X(
        self,
        steps: int,
        last_window: Optional[Union[pd.Series, pd.DataFrame]] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None
    ) -> pd.DataFrame:
        """
        Create the predictors needed to predict `steps` ahead. As it is a recursive
        process, the predictors are created at each iteration of the prediction 
        process.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, pandas DataFrame, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in `self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.

        Returns
        -------
        X_predict : pandas DataFrame
            Pandas DataFrame with the predictors for each step. The index 
            is the same as the prediction index.
        
        """
        
        predictions = self.predict(
                          steps       = steps,
                          last_window = last_window,
                          exog        = exog
                      )

        last_window_values, exog_values, prediction_index = self._create_predict_inputs(
            steps=steps, last_window=last_window, exog=exog, check_inputs=False
        )
        
        full_predictors = np.concatenate((last_window_values, predictions))
        idx = np.arange(-steps, 0)[:, None] - self.lags
        X_predict = full_predictors[idx + len(full_predictors)]
        if exog is not None:
            X_predict = np.concatenate([X_predict, exog_values], axis=1)

        X_predict = pd.DataFrame(
                        data    = X_predict,
                        columns = self.X_train_features_names_out_,
                        index   = prediction_index
                    )

        return X_predict


    def predict(
        self,
        steps: int,
        last_window: Optional[Union[pd.Series, pd.DataFrame]] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None,
        check_inputs: bool = True
    ) -> pd.Series:
        """
        Predict n steps ahead. It is an recursive process in which, each prediction,
        is used as a predictor for the next step.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, pandas DataFrame, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in `self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.
        check_inputs : bool, default `True`
            If `True`, the input is checked for possible warnings and errors 
            with the `check_predict_input` function. This argument is created 
            for internal use and is not recommended to be changed.

        Returns
        -------
        predictions : pandas Series
            Predicted values.
        
        """

        last_window_values, exog_values, prediction_index  = self._create_predict_inputs(
            steps=steps, last_window=last_window, exog=exog, check_inputs=check_inputs
        )
        
        predictions = self._recursive_predict(
                          steps       = steps,
                          last_window = last_window_values,
                          exog        = exog_values
                      )

        if self.differentiation is not None:
            predictions = self.differentiator.inverse_transform_next_window(predictions)

        predictions = transform_numpy(
                          array             = predictions,
                          transformer       = self.transformer_y,
                          fit               = False,
                          inverse_transform = True
                      )

        predictions = pd.Series(
                          data  = predictions,
                          index = prediction_index,
                          name = 'pred'
                      )

        return predictions


    # TODO: change in_sample_residuals to use_in_sample_residuals
    # TODO: change binned_residuals to use_binned_residuals
    def predict_bootstrapping(
        self,
        steps: int,
        last_window: Optional[pd.Series] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None,
        n_boot: int = 250,
        random_state: int = 123,
        in_sample_residuals: bool = True,
        binned_residuals: bool = False
    ) -> pd.DataFrame:
        """
        Generate multiple forecasting predictions using a bootstrapping process. 
        By sampling from a collection of past observed errors (the residuals),
        each iteration of bootstrapping generates a different set of predictions. 
        See the Notes section for more information. 
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in `self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.
        n_boot : int, default `500`
            Number of bootstrapping iterations used to estimate predictions.
        random_state : int, default `123`
            Sets a seed to the random generator, so that boot predictions are always 
            deterministic.
        in_sample_residuals : bool, default `True`
            If `True`, residuals from the training data are used as proxy of
            prediction error to create predictions. If `False`, out of sample 
            residuals are used. In the latter case, the user should have
            calculated and stored the residuals within the forecaster (see
            `set_out_sample_residuals()`).
        binned_residuals : bool, default `False`
            If `True`, residuals used in each bootstrapping iteration are selected
            conditioning on the predicted values. If `False`, residuals are selected
            randomly without conditioning on the predicted values.
            **WARNING: This argument is newly introduced and requires special attention.
            It is still experimental and may undergo changes.
            **New in version 0.12.0**

        Returns
        -------
        boot_predictions : pandas DataFrame
            Predictions generated by bootstrapping.
            Shape: (steps, n_boot)

        Notes
        -----
        More information about prediction intervals in forecasting:
        https://otexts.com/fpp3/prediction-intervals.html#prediction-intervals-from-bootstrapped-residuals
        Forecasting: Principles and Practice (3nd ed) Rob J Hyndman and George Athanasopoulos.

        """

        (
            last_window_values,
            exog_values,
            prediction_index
        ) = self._create_predict_inputs(
            steps               = steps, 
            last_window         = last_window, 
            exog                = exog,
            predict_boot        = True, 
            in_sample_residuals = in_sample_residuals,
            binned_residuals    = binned_residuals
        )

        if in_sample_residuals:
            residuals = self.in_sample_residuals_
            residuals_by_bin = self.in_sample_residuals_by_bin_
        else:
            residuals = self.out_sample_residuals_
            residuals_by_bin = self.out_sample_residuals_by_bin_

        rng = np.random.default_rng(seed=random_state)
        if not binned_residuals:
            sampled_residuals = rng.choice(
                                    a       = residuals,
                                    size    = (steps, n_boot),
                                    replace = True
                                )
        
        boot_columns = []
        boot_predictions = np.full(
                               shape      = (steps, n_boot),
                               fill_value = np.nan,
                               dtype      = float
                           )
        for i in range(n_boot):

            boot_columns.append(f"pred_boot_{i}")
            boot_predictions[:, i] = self._recursive_predict(
                steps            = steps,
                last_window      = last_window_values,
                exog             = exog_values,
                residuals        = residuals_by_bin if binned_residuals else sampled_residuals[:, i],
                binned_residuals = binned_residuals,
                rng              = rng
            )

        if self.differentiation is not None:
            boot_predictions = (
                self.differentiator.inverse_transform_next_window(boot_predictions)
            )
        
        boot_predictions = np.apply_along_axis(
                               func1d            = transform_numpy,
                               axis              = 0,
                               arr               = boot_predictions,
                               transformer       = self.transformer_y,
                               fit               = False,
                               inverse_transform = True
                           )

        boot_predictions = pd.DataFrame(
                               data    = boot_predictions,
                               index   = prediction_index,
                               columns = boot_columns
                           )

        return boot_predictions


    def predict_interval(
        self,
        steps: int,
        last_window: Optional[pd.Series] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None,
        interval: list = [5, 95],
        n_boot: int = 250,
        random_state: int = 123,
        in_sample_residuals: bool = True,
        binned_residuals: bool = False
    ) -> pd.DataFrame:
        """
        Iterative process in which each prediction is used as a predictor
        for the next step, and bootstrapping is used to estimate prediction
        intervals. Both predictions and intervals are returned.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in` self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.
        interval : list, default `[5, 95]`
            Confidence of the prediction interval estimated. Sequence of 
            percentiles to compute, which must be between 0 and 100 inclusive. 
            For example, interval of 95% should be as `interval = [2.5, 97.5]`.
        n_boot : int, default `500`
            Number of bootstrapping iterations used to estimate predictions.
        random_state : int, default `123`
            Sets a seed to the random generator, so that boot predictions are always 
            deterministic.
        in_sample_residuals : bool, default `True`
            If `True`, residuals from the training data are used as proxy of
            prediction error to create predictions. If `False`, out of sample 
            residuals are used. In the latter case, the user should have
            calculated and stored the residuals within the forecaster (see
            `set_out_sample_residuals()`).
        binned_residuals : bool, default `False`
            If `True`, residuals used in each bootstrapping iteration are selected
            conditioning on the predicted values. If `False`, residuals are selected
            randomly without conditioning on the predicted values.
            **WARNING: This argument is newly introduced and requires special attention.
            It is still experimental and may undergo changes.
            **New in version 0.12.0**

        Returns
        -------
        predictions : pandas DataFrame
            Values predicted by the forecaster and their estimated interval.

            - pred: predictions.
            - lower_bound: lower bound of the interval.
            - upper_bound: upper bound of the interval.

        Notes
        -----
        More information about prediction intervals in forecasting:
        https://otexts.com/fpp2/prediction-intervals.html
        Forecasting: Principles and Practice (2nd ed) Rob J Hyndman and
        George Athanasopoulos.
        
        """

        check_interval(interval=interval)

        boot_predictions = self.predict_bootstrapping(
                               steps               = steps,
                               last_window         = last_window,
                               exog                = exog,
                               n_boot              = n_boot,
                               random_state        = random_state,
                               in_sample_residuals = in_sample_residuals,
                               binned_residuals    = binned_residuals
                           )

        predictions = self.predict(
                          steps        = steps,
                          last_window  = last_window,
                          exog         = exog,
                          check_inputs = False
                      )

        interval = np.array(interval) / 100
        predictions_interval = boot_predictions.quantile(q=interval, axis=1).transpose()
        predictions_interval.columns = ['lower_bound', 'upper_bound']
        predictions = pd.concat((predictions, predictions_interval), axis=1)

        return predictions


    def predict_quantiles(
        self,
        steps: int,
        last_window: Optional[pd.Series] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None,
        quantiles: list = [0.05, 0.5, 0.95],
        n_boot: int = 250,
        random_state: int = 123,
        in_sample_residuals: bool = True,
        binned_residuals: bool = False
    ) -> pd.DataFrame:
        """
        Calculate the specified quantiles for each step. After generating 
        multiple forecasting predictions through a bootstrapping process, each 
        quantile is calculated for each step.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in` self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.
        quantiles : list, default `[0.05, 0.5, 0.95]`
            Sequence of quantiles to compute, which must be between 0 and 1 
            inclusive. For example, quantiles of 0.05, 0.5 and 0.95 should be as 
            `quantiles = [0.05, 0.5, 0.95]`.
        n_boot : int, default `500`
            Number of bootstrapping iterations used to estimate quantiles.
        random_state : int, default `123`
            Sets a seed to the random generator, so that boot quantiles are always 
            deterministic.
        in_sample_residuals : bool, default `True`
            If `True`, residuals from the training data are used as proxy of
            prediction error to create prediction quantiles. If `False`, out of
            sample residuals are used. In the latter case, the user should have
            calculated and stored the residuals within the forecaster (see
            `set_out_sample_residuals()`).
        binned_residuals : bool, default `False`
            If `True`, residuals used in each bootstrapping iteration are selected
            conditioning on the predicted values. If `False`, residuals are selected
            randomly without conditioning on the predicted values.
            **WARNING: This argument is newly introduced and requires special attention.
            It is still experimental and may undergo changes.
            **New in version 0.12.0**

        Returns
        -------
        predictions : pandas DataFrame
            Quantiles predicted by the forecaster.

        Notes
        -----
        More information about prediction intervals in forecasting:
        https://otexts.com/fpp2/prediction-intervals.html
        Forecasting: Principles and Practice (2nd ed) Rob J Hyndman and
        George Athanasopoulos.
        
        """

        check_interval(quantiles=quantiles)

        boot_predictions = self.predict_bootstrapping(
                               steps               = steps,
                               last_window         = last_window,
                               exog                = exog,
                               n_boot              = n_boot,
                               random_state        = random_state,
                               in_sample_residuals = in_sample_residuals,
                               binned_residuals    = binned_residuals
                           )

        predictions = boot_predictions.quantile(q=quantiles, axis=1).transpose()
        predictions.columns = [f'q_{q}' for q in quantiles]

        return predictions


    def predict_dist(
        self,
        steps: int,
        distribution: object,
        last_window: Optional[pd.Series] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None,
        n_boot: int = 250,
        random_state: int = 123,
        in_sample_residuals: bool = True,
        binned_residuals: bool = False
    ) -> pd.DataFrame:
        """
        Fit a given probability distribution for each step. After generating 
        multiple forecasting predictions through a bootstrapping process, each 
        step is fitted to the given distribution.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        distribution : Object
            A distribution object from scipy.stats.
        last_window : pandas Series, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).  
            If `last_window = None`, the values stored in` self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.
        n_boot : int, default `500`
            Number of bootstrapping iterations used to estimate predictions.
        random_state : int, default `123`
            Sets a seed to the random generator, so that boot predictions are always 
            deterministic.
        in_sample_residuals : bool, default `True`
            If `True`, residuals from the training data are used as proxy of
            prediction error to create predictions. If `False`, out of sample 
            residuals are used. In the latter case, the user should have
            calculated and stored the residuals within the forecaster (see
            `set_out_sample_residuals()`).
        binned_residuals : bool, default `False`
            If `True`, residuals used in each bootstrapping iteration are selected
            conditioning on the predicted values. If `False`, residuals are selected
            randomly without conditioning on the predicted values.
            **WARNING: This argument is newly introduced and requires special attention.
            It is still experimental and may undergo changes.
            **New in version 0.12.0**

        Returns
        -------
        predictions : pandas DataFrame
            Distribution parameters estimated for each step.

        """

        boot_samples = self.predict_bootstrapping(
                           steps               = steps,
                           last_window         = last_window,
                           exog                = exog,
                           n_boot              = n_boot,
                           random_state        = random_state,
                           in_sample_residuals = in_sample_residuals,
                           binned_residuals    = binned_residuals
                       )       

        param_names = [p for p in inspect.signature(distribution._pdf).parameters
                       if not p == 'x'] + ["loc", "scale"]
        param_values = np.apply_along_axis(
                           lambda x: distribution.fit(x),
                           axis = 1,
                           arr  = boot_samples
                       )
        predictions = pd.DataFrame(
                          data    = param_values,
                          columns = param_names,
                          index   = boot_samples.index
                      )

        return predictions


    def set_params(
        self, 
        params: dict
    ) -> None:
        """
        Set new values to the parameters of the scikit learn model stored in the
        forecaster.
        
        Parameters
        ----------
        params : dict
            Parameters values.

        Returns
        -------
        None
        
        """

        self.regressor = clone(self.regressor)
        self.regressor.set_params(**params)


    def set_fit_kwargs(
        self, 
        fit_kwargs: dict
    ) -> None:
        """
        Set new values for the additional keyword arguments passed to the `fit` 
        method of the regressor.
        
        Parameters
        ----------
        fit_kwargs : dict
            Dict of the form {"argument": new_value}.

        Returns
        -------
        None
        
        """

        self.fit_kwargs = check_select_fit_kwargs(self.regressor, fit_kwargs=fit_kwargs)


    def set_lags(
        self, 
        lags: Union[int, list, np.ndarray, range]
    ) -> None:
        """
        Set new value to the attribute `lags`. Attributes `max_lag`, 
        `window_size` and  `window_size_diff` are also updated.
        
        Parameters
        ----------
        lags : int, list, numpy ndarray, range
            Lags used as predictors. Index starts at 1, so lag 1 is equal to t-1.

            - `int`: include lags from 1 to `lags` (included).
            - `list`, `1d numpy ndarray` or `range`: include only lags present in 
            `lags`, all elements must be int.

        Returns
        -------
        None
        
        """

        self.lags = initialize_lags(type(self).__name__, lags)
        self.max_lag = max(self.lags)
        self.window_size = max(self.lags)
        self.window_size_diff = max(self.lags)
        if self.differentiation is not None:
            self.window_size_diff += self.differentiation        


    def set_out_sample_residuals(
        self, 
        residuals: Union[pd.Series, np.ndarray],
        y_pred: Optional[Union[pd.Series, np.ndarray]] = None,
        append: bool = True,
        transform: bool = True,
        random_state: int = 123
    ) -> None:
        """
        Set new values to the attribute `out_sample_residuals_`. Out of sample
        residuals are meant to be calculated using observations that did not
        participate in the training process. If `y_pred` is provided, residuals
        are binned according to the predicted value they are associated with. If
        `y_pred` is `None`, residuals are stored without binning. Only up to 200
        residuals are stored per bin.
        
        Parameters
        ----------
        residuals : pandas Series, numpy ndarray
            Values of residuals. If `y_pred` is `None`, at most 1000 values are
            stored. If `y_pred` is not `None`, at most 200 * n_bins values are
            stored, where `n_bins` is the number of bins used in `self.binner`.
        y_pred : pandas Series, numpy ndarray, default `None`
            Predicted values of the time series from which the residuals have been
            calculated. This argument is used to bin residuals according to the
            predicted values. `y_pred` and `residuals` must be of the same class
            (both pandas Series or both numpy ndarray) must have the same length
            and, if they are pandas Series, the same index. 
            
            - If `y_pred` is `None`, residuals are not binned.
            - If affter binning, a bin has more than 200 residuals, only a random
                sample of 200 residuals is stored.
            - If affter binning, a bin binning is empty, it is filled with a
            random sample of residuals from other bins. This is done to ensure
            that all bins have at least one residual and can be used in the
            prediction process.
            **New in version 0.12.0**
        append : bool, default `True`
            If `True`, new residuals are added to the once already stored in the
            forecaster. Once the limit of 200 values per bin is reached, no more values
            are appended. If False, stored residuals are overwritten with the new
            residuals.
        transform : bool, default `True`
            If `True`, new residuals are transformed using self.transformer_y.
        random_state : int, default `123`
            Sets a seed to the random sampling for reproducible output.

        Returns
        -------
        None

        """

        if not isinstance(residuals, (np.ndarray, pd.Series)):
            raise TypeError(
                (f"`residuals` argument must be `numpy ndarray` or `pandas Series`, "
                 f"but found {type(residuals)}.")
            )

        if not isinstance(y_pred, (np.ndarray, pd.Series, type(None))):
            raise TypeError(
                (f"`y_pred` argument must be `numpy ndarray`, `pandas Series` or `None`, "
                 f"but found {type(y_pred)}.")
            )

        if y_pred is not None and len(residuals) != len(y_pred):
            raise ValueError(
                (f"`residuals` and `y_pred` must have the same length, but found "
                 f"{len(residuals)} and {len(y_pred)}.")
            )

        if isinstance(residuals, pd.Series) and isinstance(y_pred, pd.Series):
            if not residuals.index.equals(y_pred.index):
                raise ValueError(
                    (f"`residuals` and `y_pred` must have the same index, but found "
                     f"{residuals.index} and {y_pred.index}.")
                )

        if y_pred is not None and not self.is_fitted:
            raise NotFittedError(
                ("This forecaster is not fitted yet. Call `fit` with appropriate "
                 "arguments before using `set_out_sample_residuals()`.")
            )

        if isinstance(residuals, np.ndarray):
            residuals = pd.Series(residuals, name='residuals')
        else:
            residuals = residuals.rename('residuals').reset_index(drop=True)

        if isinstance(y_pred, np.ndarray):
            y_pred = pd.Series(y_pred, name='prediction')
        elif isinstance(y_pred, pd.Series):
            y_pred = y_pred.rename('prediction').reset_index(drop=True)

        if not transform and self.transformer_y is not None:
            warnings.warn(
                (f"Argument `transform` is set to `False` but forecaster was trained "
                 f"using a transformer {self.transformer_y}. Ensure that the new residuals "
                 f"are already transformed or set `transform=True`.")
            )

        if transform and self.transformer_y is not None:
            warnings.warn(
                (f"Residuals will be transformed using the same transformer used "
                 f"when training the forecaster ({self.transformer_y}). Ensure that the "
                 f"new residuals are on the same scale as the original time series.")
            )

            residuals = transform_numpy(
                            array             = residuals,
                            transformer       = self.transformer_y,
                            fit               = False,
                            inverse_transform = False
                        )

        if y_pred is None:
            # Residuals are not binned.
            if len(residuals) > 1000:
                rng = np.random.default_rng(seed=random_state)
                residuals = rng.choice(a=residuals, size=1000, replace=False)
            if append and self.out_sample_residuals_ is not None:
                free_space = max(0, 1000 - len(self.out_sample_residuals_))
                if len(residuals) < free_space:
                    residuals = np.hstack((
                                    self.out_sample_residuals_,
                                    residuals
                                ))
                else:
                    residuals = np.hstack((
                                    self.out_sample_residuals_,
                                    residuals[:free_space]
                                ))
            self.out_sample_residuals_ = residuals
        else:
            # Residuals are binned according to the predicted values.
            data = pd.merge(
                       residuals,
                       y_pred,
                       left_index  = True,
                       right_index = True
                   )
            data['bin'] = self.binner.transform(data[['prediction']].to_numpy()).astype(int)
            residuals_by_bin = data.groupby('bin')['residuals'].apply(np.array).to_dict()

            if append and self.out_sample_residuals_by_bin_ is not None:
                for k, v in residuals_by_bin.items():
                    if k in self.out_sample_residuals_by_bin_:
                        free_space = max(0, 200 - len(self.out_sample_residuals_by_bin_[k]))
                        if len(v) < free_space:
                            self.out_sample_residuals_by_bin_[k] = np.hstack((
                                self.out_sample_residuals_by_bin_[k],
                                v
                            ))
                        else:
                            self.out_sample_residuals_by_bin_[k] = np.hstack((
                                self.out_sample_residuals_by_bin_[k],
                                v[:free_space]
                            ))
                    else:
                        self.out_sample_residuals_by_bin_[k] = v
            else:
                self.out_sample_residuals_by_bin_ = residuals_by_bin

            for k, v in self.out_sample_residuals_by_bin_.items():
                rng = np.random.default_rng(seed=123)
                if len(v) > 200:
                    # Only up to 200 residuals are stored per bin
                    sample = rng.choice(a=v, size=200, replace=False)
                    self.out_sample_residuals_by_bin_[k] = sample

            self.out_sample_residuals_ = np.concatenate(list(
                                             self.out_sample_residuals_by_bin_.values()
                                         ))

            for k in self.in_sample_residuals_by_bin_.keys():
                if k not in self.out_sample_residuals_by_bin_:
                    self.out_sample_residuals_by_bin_[k] = np.array([])

            empty_bins = [k for k, v in self.out_sample_residuals_by_bin_.items() 
                          if len(v) == 0]
            if empty_bins:
                warnings.warn(
                    (f"The following bins have no out of sample residuals: {empty_bins}. "
                     f"No predicted values fall in the interval "
                     f"{[self.binner_intervals[bin] for bin in empty_bins]}. "
                     f"Empty bins will be filled with a random sample of residuals from "
                     f"the other bins.")
                )
                for k in empty_bins:
                    rng = np.random.default_rng(seed=123)
                    self.out_sample_residuals_by_bin_[k] = rng.choice(
                                                               a       = self.out_sample_residuals_,
                                                               size    = 200,
                                                               replace = True
                                                           )


    def get_feature_importances(
        self,
        sort_importance: bool = True
    ) -> pd.DataFrame:
        """
        Return feature importances of the regressor stored in the forecaster.
        Only valid when regressor stores internally the feature importances in the
        attribute `feature_importances_` or `coef_`. Otherwise, returns `None`.

        Parameters
        ----------
        sort_importance: bool, default `True`
            If `True`, sorts the feature importances in descending order.

        Returns
        -------
        feature_importances : pandas DataFrame
            Feature importances associated with each predictor.

        """

        if not self.is_fitted:
            raise NotFittedError(
                ("This forecaster is not fitted yet. Call `fit` with appropriate "
                 "arguments before using `get_feature_importances()`.")
            )

        if isinstance(self.regressor, Pipeline):
            estimator = self.regressor[-1]
        else:
            estimator = self.regressor

        if hasattr(estimator, 'feature_importances_'):
            feature_importances = estimator.feature_importances_
        elif hasattr(estimator, 'coef_'):
            feature_importances = estimator.coef_
        else:
            warnings.warn(
                (f"Impossible to access feature importances for regressor of type "
                 f"{type(estimator)}. This method is only valid when the "
                 f"regressor stores internally the feature importances in the "
                 f"attribute `feature_importances_` or `coef_`.")
            )
            feature_importances = None

        if feature_importances is not None:
            feature_importances = pd.DataFrame({
                                      'feature': self.X_train_features_names_out_,
                                      'importance': feature_importances
                                  })
            if sort_importance:
                feature_importances = feature_importances.sort_values(
                                          by='importance', ascending=False
                                      )

        return feature_importances


    def _create_predict_inputs_013(
        self,
        steps: int,
        last_window: Optional[Union[pd.Series, pd.DataFrame]] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None
    ) -> Tuple[np.ndarray, np.ndarray, pd.Index]:
        """
        Create inputs needed for the first iteration of the prediction process. 
        Since it is a recursive process, last window is updated at each 
        iteration of the prediction process.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, pandas DataFrame, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in `self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.

        Returns
        -------
        last_window_values : numpy ndarray
            Series predictors.
        last_window_index : pandas Index
            Last window Index.
        exog_values : numpy ndarray, default `None`
            Exogenous variable/s included as predictor/s.
        
        """

        if last_window is None:
            last_window = self.last_window_

        check_predict_input(
            forecaster_name  = type(self).__name__,
            steps            = steps,
            is_fitted        = self.is_fitted,
            exog_in_         = self.exog_in_,
            index_type_      = self.index_type_,
            index_freq_      = self.index_freq_,
            window_size      = self.window_size_diff,
            last_window      = last_window,
            last_window_exog = None,
            exog             = exog,
            exog_type_in_    = self.exog_type_in_,
            exog_names_in_   = self.exog_names_in_,
            interval         = None,
            max_steps        = None
        )

        last_window = last_window.iloc[-self.window_size_diff:].copy()
        last_window = input_to_frame(data=last_window, input_name='last_window')

        if exog is not None:
            exog = input_to_frame(data=exog, input_name='exog')
            exog = exog.loc[:, self.exog_names_in_]
            exog = transform_dataframe(
                       df                = exog,
                       transformer       = self.transformer_exog,
                       fit               = False,
                       inverse_transform = False
                   )
            check_exog_dtypes(exog=exog)
            exog_values = exog.to_numpy()[:steps]
        else:
            exog_values = None

        last_window = transform_dataframe(
                          df                = last_window,
                          transformer       = self.transformer_y,
                          fit               = False,
                          inverse_transform = False
                      )
        last_window_values, last_window_index = preprocess_last_window(
                                                    last_window = last_window
                                                )
        if self.differentiation is not None:
            last_window_values = self.differentiator.fit_transform(last_window_values)

        return last_window_values, last_window_index, exog_values


    def _recursive_predict_013(
        self,
        steps: int,
        last_window: np.ndarray,
        exog: Optional[np.ndarray] = None
    ) -> np.ndarray:
        """
        Predict n steps ahead. It is an iterative process in which, each prediction,
        is used as a predictor for the next step.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : numpy ndarray
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
        exog : numpy ndarray, default `None`
            Exogenous variable/s included as predictor/s.

        Returns
        -------
        predictions : numpy ndarray
            Predicted values.
        
        """

        predictions = np.full(shape=steps, fill_value=np.nan)
        last_window = np.concatenate((last_window, predictions))

        for i in range(steps):

            X = last_window[-self.lags - (steps - i)].reshape(1, -1)
            if exog is not None:
                X = np.column_stack((X, exog[i, ].reshape(1, -1)))
        
            with warnings.catch_warnings():
                # Suppress scikit-learn warning: "X does not have valid feature names,
                # but NoOpTransformer was fitted with feature names".
                warnings.filterwarnings(
                    "ignore", 
                    message="X does not have valid feature names", 
                    category=UserWarning
                )
                prediction = self.regressor.predict(X).ravel()[0]
                predictions[i] = prediction

            # Update `last_window` values. The first position is discarded and 
            # the new prediction is added at the end.
            last_window[-(steps - i)] = prediction

        return predictions


    def create_predict_X_013(
        self,
        steps: int,
        last_window: Optional[Union[pd.Series, pd.DataFrame]] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None
    ) -> pd.DataFrame:
        """
        Create the predictors needed to predict `steps` ahead. As it is a recursive
        process, the predictors are created at each iteration of the prediction 
        process.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, pandas DataFrame, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in `self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.

        Returns
        -------
        X_predict : pandas DataFrame
            Pandas DataFrame with the predictors for each step. The index 
            is the same as the prediction index.
        
        """

        last_window_values, _, exog_values = self._create_predict_inputs_013(
            steps=steps, last_window=last_window, exog=exog
        )
        
        predictions = self.predict_013(
                          steps       = steps,
                          last_window = last_window,
                          exog        = exog
                      )
        
        full_predictors = np.concatenate((last_window_values, predictions))
        idx = np.arange(-steps, 0)[:, None] - self.lags
        X_predict = full_predictors[idx + len(full_predictors)]
        if exog is not None:
            X_predict = np.concatenate([X_predict, exog_values], axis=1)

        X_predict = pd.DataFrame(
                        data    = X_predict,
                        columns = self.X_train_features_names_out_,
                        index   = predictions.index
                    )

        return X_predict


    def predict_013(
        self,
        steps: int,
        last_window: Optional[Union[pd.Series, pd.DataFrame]] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None
    ) -> pd.Series:
        """
        Predict n steps ahead. It is an recursive process in which, each prediction,
        is used as a predictor for the next step.
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, pandas DataFrame, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in `self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.

        Returns
        -------
        predictions : pandas Series
            Predicted values.
        
        """

        last_window_values, last_window_index, exog_values = self._create_predict_inputs_013(
            steps=steps, last_window=last_window, exog=exog
        )
        
        predictions = self._recursive_predict_013(
                          steps       = steps,
                          last_window = last_window_values,
                          exog        = exog_values
                      )

        if self.differentiation is not None:
            predictions = self.differentiator.inverse_transform_next_window(predictions)

        predictions = pd.Series(
                          data  = predictions,
                          index = expand_index(
                                      index = last_window_index,
                                      steps = steps
                                  ),
                          name = 'pred'
                      )

        predictions = transform_series(
                          series            = predictions,
                          transformer       = self.transformer_y,
                          fit               = False,
                          inverse_transform = True
                      )

        return predictions


    def predict_bootstrapping_013(
        self,
        steps: int,
        last_window: Optional[pd.Series] = None,
        exog: Optional[Union[pd.Series, pd.DataFrame]] = None,
        n_boot: int = 250,
        random_state: int = 123,
        in_sample_residuals: bool = True,
        binned_residuals: bool = False,
    ) -> pd.DataFrame:
        """
        Generate multiple forecasting predictions using a bootstrapping process. 
        By sampling from a collection of past observed errors (the residuals),
        each iteration of bootstrapping generates a different set of predictions. 
        See the Notes section for more information. 
        
        Parameters
        ----------
        steps : int
            Number of future steps predicted.
        last_window : pandas Series, default `None`
            Series values used to create the predictors (lags) needed in the 
            first iteration of the prediction (t + 1).
            If `last_window = None`, the values stored in `self.last_window_` are
            used to calculate the initial predictors, and the predictions start
            right after training data.
        exog : pandas Series, pandas DataFrame, default `None`
            Exogenous variable/s included as predictor/s.
        n_boot : int, default `500`
            Number of bootstrapping iterations used to estimate predictions.
        random_state : int, default `123`
            Sets a seed to the random generator, so that boot predictions are always 
            deterministic.
        in_sample_residuals : bool, default `True`
            If `True`, residuals from the training data are used as proxy of
            prediction error to create predictions. If `False`, out of sample 
            residuals are used. In the latter case, the user should have
            calculated and stored the residuals within the forecaster (see
            `set_out_sample_residuals()`).
        binned_residuals : bool, default `False`
            If `True`, residuals used in each bootstrapping iteration are selected
            conditioning on the predicted values. If `False`, residuals are selected
            randomly without conditioning on the predicted values.
            **WARNING: This argument is newly introduced and requires special attention.
            It is still experimental and may undergo changes.
            **New in version 0.12.0**

        Returns
        -------
        boot_predictions : pandas DataFrame
            Predictions generated by bootstrapping.
            Shape: (steps, n_boot)

        Notes
        -----
        More information about prediction intervals in forecasting:
        https://otexts.com/fpp3/prediction-intervals.html#prediction-intervals-from-bootstrapped-residuals
        Forecasting: Principles and Practice (3nd ed) Rob J Hyndman and George Athanasopoulos.

        """

        # TODO: Move to check_predict_input(), validate why it was not there.
        if not in_sample_residuals:
            if not binned_residuals and self.out_sample_residuals_ is None:
                raise ValueError(
                    ("`forecaster.out_sample_residuals_` is `None`. Use "
                     "`in_sample_residuals=True` or the `set_out_sample_residuals()` "
                     "method before predicting.")
                )
            if binned_residuals and self.out_sample_residuals_by_bin_ is None:
                raise ValueError(
                    ("`forecaster.out_sample_residuals_by_bin_` is `None`. Use "
                     "`in_sample_residuals=True` or the `set_out_sample_residuals()` "
                     "method before predicting.")
                )

        last_window_values, last_window_index, exog_values = self._create_predict_inputs_013(
            steps=steps, last_window=last_window, exog=exog
        )

        boot_predictions = np.full(
                               shape      = (steps, n_boot),
                               fill_value = np.nan,
                               dtype      = float
                           )
        rng = np.random.default_rng(seed=random_state)
        seeds = rng.integers(low=0, high=10000, size=n_boot)

        if in_sample_residuals:
            residuals = self.in_sample_residuals_
            residuals_by_bin = self.in_sample_residuals_by_bin_
        else:
            residuals = self.out_sample_residuals_
            residuals_by_bin = self.out_sample_residuals_by_bin_

        for i in range(n_boot):
            # In each bootstraping iteration the initial last_window and exog
            # need to be restored.
            last_window_boot = last_window_values.copy()
            exog_boot = exog_values.copy() if exog is not None else None

            rng = np.random.default_rng(seed=seeds[i])
            if not binned_residuals:
                sampled_residuals = rng.choice(
                                        a       = residuals,
                                        size    = steps,
                                        replace = True
                                    )

            for step in range(steps):

                prediction = self._recursive_predict_013(
                                 steps       = 1,
                                 last_window = last_window_boot,
                                 exog        = exog_boot
                             )
                if binned_residuals:
                    predicted_bin = (
                        self.binner.transform(prediction.reshape(1, -1)).astype(int)[0][0]
                    )
                    sampled_residual = rng.choice(a=residuals_by_bin[predicted_bin], size=1)
                else:
                    sampled_residual = sampled_residuals[step]

                prediction_with_residual  = prediction + sampled_residual
                boot_predictions[step, i] = prediction_with_residual[0]
                last_window_boot = np.append(
                                       last_window_boot[1:],
                                       prediction_with_residual
                                   )
                if exog is not None:
                    exog_boot = exog_boot[1:]

            if self.differentiation is not None:
                boot_predictions[:, i] = (
                    self.differentiator.inverse_transform_next_window(boot_predictions[:, i])
                )

        boot_predictions = pd.DataFrame(
                               data    = boot_predictions,
                               index   = expand_index(last_window_index, steps=steps),
                               columns = [f"pred_boot_{i}" for i in range(n_boot)]
                           )

        if self.transformer_y:
            for col in boot_predictions.columns:
                boot_predictions[col] = transform_series(
                                            series            = boot_predictions[col],
                                            transformer       = self.transformer_y,
                                            fit               = False,
                                            inverse_transform = True
                                        )

        return boot_predictions
