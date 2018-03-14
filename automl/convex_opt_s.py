"""
Find columns of error matrix to minimize variance of predicted latent features.
Solves convex optimization problem as described in chapter 7.5 in https://web.stanford.edu/~boyd/cvxbook/bv_cvxbook.pdf
"""

import numpy as np
import os
import pandas as pd
import pickle
import openml
from multiprocessing import cpu_count
from scipy.optimize import minimize
from sklearn.preprocessing import PolynomialFeatures
from sklearn.linear_model import LinearRegression


def solve(t_predicted, t_max, Y, n_cores=None):
    """Solve the following optimization problem:
    minimize -log(det(sum_i v[i]*Y[:, i]*Y[:, i].T)) subject to 0 <= v[i] <= 1 and t_predicted.T * v <= t_max
    The optimal vector v is an approximation of a boolean vector indicating which entries to sample.

    Args:
         t_predicted (np.ndarray): 1-d array specifying predicted runtime for each model setting
         t_max (float):            maximum runtime of sampled model
         Y (np.ndarray):           matrix representing latent variable weights of error matrix
         n_cores (int):            The number of cores as resource limit.
    Returns:
        np.ndarray:                optimal vector v (not truncated to binary values)
    """
    if n_cores == None:
        n_cores = cpu_count()
    
    def objective(v):
        sign, log_det = np.linalg.slogdet(Y @ np.diag(v) @ Y.T)
        return -1 * sign * log_det

    def constraint(v):
        return t_max * n_cores - t_predicted @ v

    n = len(t_predicted)
    v0 = np.full((n, ), 0.5)
    constraints = {'type': 'ineq', 'fun': constraint}
    v_opt = minimize(objective, v0, method='SLSQP', bounds=[(0, 1)] * n, constraints=constraints)
    return v_opt.x


def predict_runtime(size, log=True, saved_model=None):
    """Predict the runtime for each model setting on a dataset with given shape.

    Args:
        size (np.ndarray): 1-d array specifying dataset size as [n_rows, n_columns]
        log (Boolean): whether to take logarithms of runtime when fitting.
        saved_model (str): path to pre-trained model; defaults to None
    Returns:
        np.ndarray:        1-d array of predicted runtimes
    """
    assert len(size) == 2, "Dataset must be 2-dimensional."
    shape = np.array(size)

    if saved_model:
        with open(saved_model, 'rb') as file:
            model = pickle.load(file)
        return model.predict(shape)

    defaults_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'defaults')
    try:
        dataset_sizes = pd.read_csv(os.path.join(defaults_path, 'dataset_sizes.csv'), index_col=0)
        sizes_index = np.array(dataset_sizes.index)
        sizes = dataset_sizes.values
    except FileNotFoundError:
        sizes_index = []
        sizes = []
    runtime_matrix = pd.read_csv(os.path.join(defaults_path, 'runtime_matrix.csv'), index_col=0)
    runtimes_index = np.array(runtime_matrix.index)
    runtimes = runtime_matrix.values
    if log:
        model = RuntimePredictor(3, sizes, sizes_index, np.log(runtimes), runtimes_index)
    else:
        model = RuntimePredictor(3, sizes, sizes_index, runtimes, runtimes_index)
    with open(os.path.join(defaults_path, 'runtime_predictor.pkl'), 'wb') as file:
        pickle.dump(model, file)

    if log:
        return np.exp(model.predict(shape))
    else:
        return model.predict(shape)


class RuntimePredictor:
    """Model that predicts the runtime for each model setting on a dataset with given shape. Performs polynomial
    regression on n (# samples), p (# features), and log(n).

    Attributes:
        degree (int):   degree of polynomial basis function
        n_models (int): number of model settings
        models (list):  list of scikit-learn LinearRegression models
    """
    def __init__(self, degree, sizes, sizes_index, runtimes, runtimes_index):
        self.degree = degree
        self.n_models = runtimes.shape[1]
        self.models = [None, ] * self.n_models
        self.fit(sizes, sizes_index, runtimes, runtimes_index)

    def fit(self, sizes, sizes_index, runtimes, runtimes_index):
        """Fit polynomial regression on pre-recorded runtimes on datasets."""
#        assert sizes.shape[0] == runtimes.shape[0], "Dataset sizes and runtimes must be recorded on same datasets."
        for i in set(runtimes_index).difference(set(sizes_index)):
            dataset=openml.datasets.get_dataset(i)
            data_numeric, data_labels, categorical = dataset.get_data(target=dataset.default_target_attribute,return_categorical_indicator=True)
            if len(sizes) == 0:
                sizes = np.array([data_numeric.shape])
                sizes_index = np.array(i)
            else:
                sizes = np.concatenate((sizes, np.array([data_numeric.shape])))
                sizes_index = np.append(sizes_index, i)

        sizes_train = np.array([sizes[list(sizes_index).index(i), :] for i in runtimes_index])
        sizes_log = np.concatenate((sizes_train, np.log(sizes_train[:, 0]).reshape(-1, 1)), axis=1)
        sizes_train_poly = PolynomialFeatures(self.degree).fit_transform(sizes_log)

        # train independent regression model to predict each runtime of each model setting
        for i in range(self.n_models):
            runtime = runtimes[:, i]
            self.models[i] = LinearRegression().fit(sizes_train_poly, runtime)

    def predict(self, size):
        """Predict runtime of all model settings on a dataset of given size.
        
        Args:
            size(np.array): Size of the dataset to fit runtime onto.
        Returns:
            predictions (np.array): The predicted runtime.
        """
        size_test = np.append(size, np.log(size[0]))
        size_test_poly = PolynomialFeatures(self.degree).fit_transform([size_test])
        predictions = np.zeros(self.n_models)
        for i in range(self.n_models):
            predictions[i] = self.models[i].predict(size_test_poly)[0]
        return predictions