from __future__ import print_function
from __future__ import division
from collections import namedtuple
import numpy as np
import open3d as o3
from . import _gmmtree
from . import transformation as tf
from . import se3_op as so
from .log import log
from functools import partial
from multiprocess import Pool

EstepResult = namedtuple('EstepResult', ['moments'])
MstepResult = namedtuple('MstepResult', ['transformation', 'q'])
Parameters  = namedtuple('Parameters', ['priors', 'centers', 'covariances'])

def bestcenter(d, centers):
    return np.argmin(np.linalg.norm(centers - d, axis=1)**2)

class GMMTree():
    """GMM Tree

    Args:
        source (numpy.ndarray, optional): Source point cloud data.
        tree_level (int, optional): Maximum depth level of GMM tree.
        lambda_c (float, optional): Parameter that determine the pruning of GMM tree.
        lambda_s (float, optional): Parameter that tolerance for building GMM tree.
        tf_init_params (dict, optional): Parameters to initialize transformation.
    """
    def __init__(self, source=None, tree_level=2, lambda_c=0.01,
                 lambda_s=0.001, tf_init_params={}):
        self._source = source
        self._tree_level = tree_level
        self._lambda_c = lambda_c
        self._lambda_s = lambda_s
        self._tf_type = tf.RigidTransformation
        self._tf_result = self._tf_type(**tf_init_params)
        self._callbacks = []
        self.n_nodes = [int(8 * (1 - 8**i) / (1 - 8)) for i in np.arange(1, self._tree_level+1)]
        if not self._source is None:
            self._nodes = _gmmtree.build_gmmtree(self._source,
                                                 self._tree_level,
                                                 self._lambda_s, 1.0e-4)

    def set_source(self, source):
        self._source = source
        self._nodes = _gmmtree.build_gmmtree(self._source,
                                             self._tree_level,
                                             self._lambda_s, 1.0e-4)

    def set_callbacks(self, callbacks):
        self._callbacks = callbacks

    def expectation_step(self, target):
        res = _gmmtree.gmmtree_reg_estep(target, self._nodes,
                                         self._tree_level, self._lambda_c)
        return EstepResult(res)

    def maximization_step(self, estep_res, trans_p):
        moments = estep_res.moments
        n = len(moments)
        amat = np.zeros((n * 3, 6))
        bmat = np.zeros(n * 3)
        for i, m in enumerate(moments):
            if m[0] < np.finfo(np.float32).eps:
                continue
            lmd, nn = np.linalg.eigh(self._nodes[i][2])
            s = m[1] / m[0]
            nn = np.multiply(nn, np.sqrt(m[0] / lmd))
            sl = slice(3 * i, 3 * (i + 1))
            bmat[sl] = np.dot(nn.T, self._nodes[i][1]) - np.dot(nn.T, s)
            amat[sl, :3] = np.cross(s, nn.T)
            amat[sl, 3:] = nn.T
        x, q, _, _ = np.linalg.lstsq(amat, bmat, rcond=-1)
        rot, t = so.twist_mul(x, trans_p.rot, trans_p.t)
        return MstepResult(tf.RigidTransformation(rot, t), q)

    def registration(self, target, maxiter=20, tol=1.0e-4):
        q = None
        for i in range(maxiter):
            t_target = self._tf_result.transform(target)
            estep_res = self.expectation_step(t_target)
            res = self.maximization_step(estep_res, self._tf_result)
            self._tf_result = res.transformation
            for c in self._callbacks:
                c(self._tf_result.inverse())
            log.debug("Iteration: {}, Criteria: {}".format(i, res.q))
            if not q is None and abs(res.q - q) < tol:
                break
            q = res.q
        return MstepResult(self._tf_result.inverse(), res.q)

def fit(source, callbacks=[], **kargs):
    """GMMTree model fitting

    Args:
        source (numpy.ndarray): Source point cloud data.
        callback (:obj:`list` of :obj:`function`, optional): Called after each iteration.
            `callback(probreg.Transformation)`

    Keyword Args:
        tree_level (int, optional): Maximum depth level of GMM tree.
        lambda_c (float, optional): Parameter that determine the pruning of GMM tree.
        lambda_s (float, optional): Parameter that tolerance for building GMM tree.
    """
    cv = lambda x: np.asarray(x.points if isinstance(x, o3.geometry.PointCloud) else x)
    gt = GMMTree(cv(source), **kargs)
    gt.set_callbacks(callbacks)
    priors = np.array([row[0] for row in gt._nodes])
    centers = np.array([row[1] for row in gt._nodes])
    covariances = np.array([row[2] for row in gt._nodes])
    n_nodes = [int(8 * (1 - 8**i) / (1 - 8)) for i in np.arange(1,gt._tree_level+1)]
    n_nodes[1:] = [y - x for x,y in zip(n_nodes,n_nodes[1:])]
    return (gt, Parameters(priors, centers, covariances), n_nodes)

def predict(gt, data, tree_level, parallel=False):
    """Inference based on the given GMMTree

    Args:
        gt (GMMTree): GMM model
        source (numpy.ndarray): Target point cloud data.
    """
    assert tree_level <= gt._tree_level and tree_level > 0, "tree_level starts at level 1 and goes up to the maximum level in the model"
    cv = lambda x: np.asarray(x.points if isinstance(x, o3.geometry.PointCloud) else x)

    if tree_level == 1:
        centers = np.array([row[1] for row in gt._nodes])[:gt.n_nodes[tree_level-1]]
    else:
        centers = np.array([row[1] for row in gt._nodes])[gt.n_nodes[tree_level-2]:gt.n_nodes[tree_level-1]]

    if parallel:
        pool = Pool()
        labels = pool.map(partial(bestcenter, centers=centers), data)
    else:
        labels = np.zeros(data.shape[0], dtype=int)
        for i, d in enumerate(data):
            labels[i] = np.argmin(np.linalg.norm(centers -d, axis=1)**2)

    return labels

def registration_gmmtree(source, target, maxiter=20, tol=1.0e-4,
                         callbacks=[], **kargs):
    """GMMTree registration

    Args:
        source (numpy.ndarray): Source point cloud data.
        target (numpy.ndarray): Target point cloud data.
        maxitr (int, optional): Maximum number of iterations to EM algorithm.
        tol (float, optional): Tolerance for termination.
        callback (:obj:`list` of :obj:`function`, optional): Called after each iteration.
            `callback(probreg.Transformation)`

    Keyword Args:
        tree_level (int, optional): Maximum depth level of GMM tree.
        lambda_c (float, optional): Parameter that determine the pruning of GMM tree.
        lambda_s (float, optional): Parameter that tolerance for building GMM tree.
        tf_init_params (dict, optional): Parameters to initialize transformation.
    """
    cv = lambda x: np.asarray(x.points if isinstance(x, o3.geometry.PointCloud) else x)
    gt = GMMTree(cv(source), **kargs)
    gt.set_callbacks(callbacks)
    return gt.registration(cv(target), maxiter, tol)
