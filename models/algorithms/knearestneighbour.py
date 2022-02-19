"""K-Nearest Neighbor implementation by sklearn. For small data (< 200k rows)."""
import os

import datatable as dt
import numpy as np
import pandas as pd
from h2oaicore.models_main import MainModel
from h2oaicore.systemutils import config, physical_cores_count, loggerinfo
from sklearn.preprocessing import LabelEncoder
from h2oaicore.models import CustomModel
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import KNeighborsRegressor, KNeighborsClassifier


class KNearestNeighbourModel(CustomModel):
    _regression = True
    _binary = True
    _multiclass = True
    _parallel_task = True
    _testing_can_skip_failure = False  # ensure tested as if shouldn't fail

    _display_name = "KNearestNeighbour"
    _description = "K Nearest Neighbour Model based on sklearn. Not adviced if the data is larger than 200K rows"

    @staticmethod
    def can_use(accuracy, interpretability, train_shape=None, test_shape=None, valid_shape=None, n_gpus=0,
                num_classes=None, **kwargs):
        if config.hard_asserts:
            # for bigger data, too slow to test even with 1 iteration
            use = train_shape is not None and train_shape[0] * train_shape[1] < 1024 * 1024 or \
                  valid_shape is not None and valid_shape[0] * valid_shape[1] < 1024 * 1024
            # too slow for walmart with only 421k x 15 even with 10 neighbors
            use &= train_shape is not None and train_shape[1] < 10
            return use
        else:
            return True

    def set_default_params(self, accuracy=None, time_tolerance=None, interpretability=None, **kwargs):
        kwargs.pop('get_best', None)
        self.mutate_params(accuracy=accuracy, time_tolerance=time_tolerance, interpretability=interpretability,
                           get_best=True, **kwargs)

    def mutate_params(self, accuracy=10, time_tolerance=10, interpretability=1, get_best=False, **kwargs):
        # Modify certain parameters for tuning
        user_choice = config.recipe_dict.copy()
        self.params = dict()
        trial = kwargs.get('trial')

        list_of_neibs = [1, 2, 5, 10, 50, 100, 150, 200]
        if config.recipe == 'kaggle':
            list_of_neibs.extend([250, 300])
        if 'GIT_HASH' in os.environ and config.hard_asserts:
            list_of_neibs = [1]
        self.params['n_neighbors'] = MainModel.get_one(list_of_neibs, get_best=get_best,
                                                       best_type="first", name="n_neighbors",
                                                       trial=trial,
                                                       user_choice=user_choice)
        self.params['metric'] = MainModel.get_one(["minkowski", "cityblock"], get_best=get_best,
                                                  best_type="first", name="metric",
                                                  trial=trial,
                                                  user_choice=user_choice)
        self.params['algorithm'] = MainModel.get_one(['auto', 'ball_tree', 'kd_tree', 'brute'], get_best=get_best,
                                                     best_type="first", name="algorithm",
                                                     trial=trial,
                                                     user_choice=user_choice)
        self.params['leaf_size'] = MainModel.get_one([30, 4, 100], get_best=get_best,
                                                     best_type="first", name="leaf_size",
                                                     trial=trial,
                                                     user_choice=user_choice)
        self.params['p'] = MainModel.get_one([2, 1], get_best=get_best,
                                             best_type="first", name="p",
                                             trial=trial,
                                             user_choice=user_choice)
        self.params['weights'] = MainModel.get_one(['uniform', 'distance'], get_best=get_best,
                                                   best_type="first", name="weights",
                                                   trial=trial,
                                                   user_choice=user_choice)
        self.params['standardize'] = MainModel.get_one([False, True], get_best=get_best,
                                                       best_type="first", name="standardize",
                                                       trial=trial,
                                                       user_choice=user_choice)

    def transcribe_params(self, params=None, **kwargs):
        """
        Fixups of params to avoid any conflicts not expressible easily for Optuna
        Or system things only need to set at fit time
        :param params:
        :return:
        """
        params_was_None = False
        if params is None:
            params = self.params  # reference, so goes back into self.params
            params_was_None = True

        # add anything that needs fixing
        if params.get('algorithm', "") not in ['ball_tree', 'kd_tree']:
            params.pop('leaf_size', None)

        if isinstance(kwargs.get('train_shape'), (tuple, list)) and kwargs.get('train_shape')[0] > 1:
            params['n_neighbors'] = min(params['n_neighbors'], kwargs.get('train_shape')[0])

        params.pop('standardize', None)  # internal parameter, not for actual underlying sklearn model

        if params_was_None:
            # in case some function didn't preserve reference
            self.params = params
        return params  # default is no transcription

    def fit(self, X, y, sample_weight=None, eval_set=None, sample_weight_eval_set=None, **kwargs):
        # system thing, doesn't need to be set in default or mutate, just at runtime in fit, into self.params so can see
        self.params["n_jobs"] = self.params_base.get('n_jobs', max(1, physical_cores_count))
        params = self.params.copy()
        params = self.transcribe_params(params, train_shape=X.shape)
        loggerinfo(self.get_logger(**kwargs), "%s fit params: %s" % (self.display_name, dict(params)))
        loggerinfo(self.get_logger(**kwargs), "%s data: %s %s" % (self.display_name, X.shape, y.shape))

        X = dt.Frame(X)
        orig_cols = list(X.names)

        if self.num_classes >= 2:
            model = KNeighborsClassifier(**params)
            lb = LabelEncoder()
            lb.fit(self.labels)
            y = lb.transform(y)
        else:
            model = KNeighborsRegressor(**params)

        X = self.basic_impute(X)
        X = X.to_numpy()
        if self.params.get('standardize', False):  # self.params since params has it popped out
            standard_scaler = StandardScaler()
            X = standard_scaler.fit_transform(X)
        else:
            standard_scaler = None

        model.fit(X, y)

        importances = self.get_basic_importances(X, y)

        self.set_model_properties(model=(model, standard_scaler, self.min),
                                  features=orig_cols,
                                  importances=importances.tolist(),  # abs(model.coef_[0])
                                  iterations=0)

    def basic_impute(self, X):
        # scikit extra trees internally converts to np.float32 during all operations,
        # so if float64 datatable, need to cast first, in case will be nan for float32
        from h2oaicore.systemutils import update_precision
        X = update_precision(X, data_type=np.float32, override_with_data_type=True, fixup_almost_numeric=True)
        # Replace missing values with a value smaller than all observed values
        if not hasattr(self, 'min') or not isinstance(self.min, dict):
            self.min = dict()
        for col in X.names:
            XX = X[:, col]
            if col not in self.min:
                self.min[col] = XX.min1()
                if self.min[col] is None or np.isnan(self.min[col]) or np.isinf(self.min[col]):
                    self.min[col] = -1e10
                else:
                    self.min[col] -= 1
            XX.replace([None, np.inf, -np.inf], self.min[col])
            X[:, col] = XX
            assert X[dt.isna(dt.f[col]), col].nrows == 0
        return X

    def get_basic_importances(self, X, y):
        # Just get feature importance using basic model so at least something to consume by genetic algorithm
        if isinstance(X, dt.Frame):
            X = X.to_numpy()
        elif isinstance(X, pd.DataFrame):
            X = X.values
        # not in self, just for importances regardless of real model behavior
        standard_scaler = StandardScaler()
        X = standard_scaler.fit_transform(X)
        from sklearn.linear_model import Ridge  # will be used to derive feature importances
        feature_model = Ridge(alpha=1., random_state=self.random_state)
        feature_model.fit(X, y)
        return np.array(abs(feature_model.coef_))

    def predict(self, X, **kwargs):
        model_tuple, _, _, _ = self.get_model_properties()
        if len(model_tuple) == 3:
            model, standard_scaler, self.min = model_tuple
        else:
            # migration for old recipe version
            model = model_tuple
            standard_scaler = None
            self.min = dict()

        X = dt.Frame(X)
        X = self.basic_impute(X)
        X = X.to_numpy()
        if standard_scaler is not None:
            X = standard_scaler.transform(X)

        pred_contribs = kwargs.get('pred_contribs', None)
        output_margin = kwargs.get('output_margin', None)

        if not pred_contribs:
            if self.num_classes == 1:
                preds = model.predict(X)
            else:
                preds = model.predict_proba(X)
                # preds = (prob_pos - prob_pos.min()) / (prob_pos.max() - prob_pos.min())
            return preds
        else:
            raise NotImplementedError("No Shapley for K-nearest model")
