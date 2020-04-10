from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

from collections import namedtuple
from math import sqrt, exp, pi, asin, acos, atan

import numpy as np
import scipy.stats as st
from scipy.integrate import quad
from scipy.stats import chi2
from scipy.special import expit, logit, xlogy
from mipframework import Algorithm, AlgorithmResult, UserError
from mipframework import TabularDataResource
from mipframework import create_runner
from mipframework.highcharts import ConfusionMatrix, ROC
from mipframework.constants import (
    P_VALUE_CUTOFF,
    P_VALUE_CUTOFF_STR,
    PREC,
    MAX_ITER,
    CONFIDENCE,
)


class CalibrationBelt(Algorithm):
    def __init__(self, cli_args):
        super(CalibrationBelt, self).__init__(__file__, cli_args, intercept=False)

    def local_init(self):
        o_vec = np.array(self.data.variables).flatten()
        e_vec = np.array(self.data.covariables).flatten()
        max_deg = int(self.parameters.max_deg)
        if not 1 < max_deg <= 4:  # todo proper condition
            raise UserError(
                "Max deg should be between 2 and 4 for `devel`=`external` "
                "or between 3 and 4 for `devel`=`internal`."
            )
        n_obs = len(e_vec)
        ge_vec = logit(e_vec)

        Y = o_vec
        X = np.array([np.power(ge_vec, i) for i in range(max_deg + 1)]).T
        Xs = np.broadcast_to(X, (4, n_obs, max_deg + 1))
        masks = np.zeros(Xs.shape, dtype=bool)
        for deg in range(0, max_deg - 1):
            masks[deg, :, deg + 2 :] = True
        Xs = np.ma.masked_array(Xs, mask=masks)

        self.store(Xs=Xs)
        self.store(Y=Y)
        self.push_and_add(n_obs=n_obs)

    def global_init(self):
        n_obs = self.fetch("n_obs")
        max_deg = int(self.parameters.max_deg)

        iter_ = 0

        lls = np.ones((max_deg,), dtype=np.float) * (-2 * n_obs * np.log(2))
        coeffs = np.zeros((max_deg, max_deg + 1))
        masks = np.zeros(coeffs.shape, dtype=bool)
        for deg in range(0, max_deg - 1):
            masks[deg, deg + 2 :] = True
        coeffs = np.ma.masked_array(coeffs, mask=masks)

        self.store(n_obs=n_obs)
        self.store(lls=lls)
        self.store(coeffs=coeffs)
        self.store(iter_=iter_)
        self.push(coeffs=coeffs)

    def local_step(self):
        Xs = self.load("Xs")
        Y = self.load("Y")
        max_deg = int(self.parameters.max_deg)
        coeffs = self.fetch("coeffs")

        # Compute 0th, 1st and 2nd derivatives of loglikelihood
        hessians = np.ma.empty((4, coeffs.shape[1], coeffs.shape[1]), dtype=np.float)
        grads = np.ma.empty((4, coeffs.shape[1]), dtype=np.float)
        lls = np.empty((4,), dtype=np.float)
        for deg in range(max_deg):
            X = Xs[deg]
            coeff = coeffs[deg]
            # Auxiliary quantities
            z = np.ma.dot(X, coeff)
            s = expit(z)
            d = np.multiply(s, (1 - s))
            D = np.diag(d)
            # Hessian
            hess = np.ma.dot(np.transpose(X), np.ma.dot(D, X))
            hessians[deg] = hess
            # Gradient
            Ymsd = (Y - s) / d  # Stable computation of (Y - s) / d
            Ymsd[(Y == 0) & (s == 0)] = -1
            Ymsd[(Y == 1) & (s == 1)] = 1
            # Ymsd = Ymsd.clip(-100, 100)

            grad = np.ma.dot(np.transpose(X), np.ma.dot(D, z + Ymsd))  # np.divide(Y -
            # s, d)
            grads[deg] = grad

            # Log-likelihood
            ll = np.sum(xlogy(Y, s) + xlogy(1 - Y, 1 - s))
            lls[deg] = ll

        self.push_and_add(lls=lls)
        self.push_and_add(grads=grads)
        self.push_and_add(hessians=hessians)

    def global_step(self):
        max_deg = int(self.parameters.max_deg)
        coeffs = self.load("coeffs")
        lls_old = self.load("lls")
        iter_ = self.load("iter_")
        grads = self.fetch("grads")
        lls = self.fetch("lls")
        hessians = self.fetch("hessians")

        for deg in range(max_deg):
            # Compute new coefficients
            inv_hess = hessians[deg]  # todo inv_hess is actually covariance
            inv_hess[: deg + 2, : deg + 2] = np.linalg.inv(
                hessians[deg][: deg + 2, : deg + 2]
            )
            coeffs[deg] = np.ma.dot(inv_hess, grads[deg])
            # coeffs[deg] = coeffs.clip(-100, 100)

            # Update termination quantities
            delta = abs(lls[deg] - lls_old[deg])
            if delta < PREC or iter_ >= MAX_ITER:
                self.terminate()
        iter_ += 1

        self.store(lls=lls)
        self.store(coeffs=coeffs)
        self.store(grads=grads)
        self.store(hessians=hessians)
        self.store(iter_=iter_)
        self.push(coeffs=coeffs)

    def local_final(self):
        Xs = self.load("Xs")
        Y = self.load("Y")
        max_deg = int(self.parameters.max_deg)
        coeffs = self.fetch("coeffs")

        # Compute partial log-likelihood on bisector,
        # i.e. coeff = [0, 1] (needed for p-value calculation)
        X = Xs[0]
        coeff = coeffs[0]
        coeff[:2] = np.array([0, 1])
        # Auxiliary quantities
        z = np.dot(X, coeff)
        s = expit(z)
        # Log-likelihood
        ls1, ls2 = np.log(s), np.log(1 - s)
        logLikBisector = np.dot(Y, ls1) + np.dot(1 - Y, ls2)

        self.push_and_add(logLikBisector=logLikBisector)

    def global_final(self):
        devel = self.parameters.devel
        thres = float(self.parameters.thres)
        max_deg = int(self.parameters.max_deg)
        num_points = int(self.parameters.num_points)

        lls = self.load("lls")
        grads = self.load("grads")
        hessians = self.load("hessians")
        coeffs = self.load("coeffs")
        logLikBisector = self.fetch("logLikBisector")

        # Perform likelihood-ratio test
        if devel == "external":
            idx = 0
        elif devel == "internal":
            idx = 1
        else:
            raise ValueError("devel should be `internal` or `external`")

        crit = chi2.ppf(q=thres, df=1)
        for i in range(idx, max_deg):
            ddev = 2 * (lls[i] - lls[i - 1])
            if ddev > crit:
                idx = i
            else:
                break

        model_deg = idx + 1

        # Get selected model coefficients, log-likelihood, grad, Hessian and covariance
        hess = hessians[idx]
        ll = lls[idx]
        coeff = coeffs[idx]
        covar = np.linalg.inv(hess[: idx + 2, : idx + 2])

        # Compute p value
        calibrationStat = 2 * (ll - logLikBisector)
        p_value = 1 - givitiStatCdf(
            calibrationStat, m=model_deg, devel=devel, thres=thres
        )

        coeff = coeff[~coeff.mask]

        # Compute calibration curve
        e_min, e_max = 0.01, 0.99  # todo
        e_lin = np.linspace(e_min, e_max, num=(int(num_points) + 1) // 2)
        e_log = expit(np.linspace(logit(e_min), logit(e_max), num=int(num_points) // 2))
        e = np.concatenate((e_lin, e_log))
        e = np.sort(e)
        ge = logit(e)
        G = [np.ones(len(e))]
        for d in range(1, len(coeff)):
            G = np.append(G, [np.power(ge, d)], axis=0)
        G = G.transpose()
        p = expit(np.dot(G, coeff))
        calib_curve = np.array([e, p]).transpose()

        # Compute confidence intervals
        cl1, cl2 = 0.8, 0.95  # todo
        GVG = np.stack([np.dot(G[i], np.dot(covar, G[i])) for i in range(len(G))])
        sqrt_chi_GVG_1 = np.sqrt(np.multiply(chi2.ppf(q=cl1, df=2), GVG))
        sqrt_chi_GVG_2 = np.sqrt(np.multiply(chi2.ppf(q=cl2, df=2), GVG))
        g_min1, g_max1 = (
            np.dot(G, coeff) - sqrt_chi_GVG_1,
            np.dot(G, coeff) + sqrt_chi_GVG_1,
        )
        g_min2, g_max2 = (
            np.dot(G, coeff) - sqrt_chi_GVG_2,
            np.dot(G, coeff) + sqrt_chi_GVG_2,
        )
        p_min1, p_max1 = expit(g_min1), expit(g_max1)
        p_min2, p_max2 = expit(g_min2), expit(g_max2)
        calib_belt1 = np.array([p_min1, p_max1])
        calib_belt2 = np.array([p_min2, p_max2])
        calib_belt1_hc = np.array([e, p_min1, p_max1]).transpose()
        calib_belt2_hc = np.array([e, p_min2, p_max2]).transpose()
        pass


def givitiStatCdf(t, m, devel="external", thres=0.95):
    assert m in {1, 2, 3, 4}, "m must be an integer from 1 to 4"
    assert 0 <= thres <= 1, "thres must be a number in [0, 1]"
    pDegInc = 1 - thres
    k = chi2.ppf(q=1 - pDegInc, df=1)
    cdfValue = None
    if devel == "external":
        if t <= (m - 1) * k:
            cdfValue = 0
        else:
            if m == 1:
                cdfValue = chi2.cdf(t, df=2)
            elif m == 2:
                cdfValue = (
                    chi2.cdf(t, df=1)
                    - 1
                    + pDegInc
                    + (-1) * sqrt(2) / sqrt(pi) * exp(-t / 2) * (sqrt(t) - sqrt(k))
                ) / pDegInc
            elif m == 3:
                integral1 = quad(
                    lambda y: (chi2.cdf(t - y, df=1) - 1 + pDegInc) * chi2.pdf(y, df=1),
                    k,
                    t - k,
                )[0]
                integral2 = quad(
                    lambda y: (sqrt(t - y) - sqrt(k)) * 1 / sqrt(y), k, t - k
                )[0]
                num = integral1 - exp(-t / 2) / (2 * pi) * 2 * integral2
                den = pDegInc ** 2
                cdfValue = num / den
            elif m == 4:
                integral = quad(
                    lambda r: r ** 2
                    * (exp(-(r ** 2) / 2) - exp(-t / 2))
                    * (
                        -pi * sqrt(k) / (2 * r)
                        + 2 * sqrt(k) / r * asin((r ** 2 / k - 1) ** (-1 / 2))
                        - 2 * atan((1 - 2 * k / r ** 2) ** (-1 / 2))
                        + 2 * sqrt(k) / r * atan((r ** 2 / k - 2) ** (-1 / 2))
                        + 2 * atan(r / sqrt(k) * sqrt(r ** 2 / k - 2))
                        - 2 * sqrt(k) / r * atan(sqrt(r ** 2 / k - 2))
                    ),
                    sqrt(3 * k),
                    sqrt(t),
                )[0]
                cdfValue = (2 / (pi * pDegInc ** 2)) ** (3 / 2) * integral
    elif devel == "internal":
        assert m != 1, "if devel=`internal`, m must be an integer from 2 to 4"
        if t <= (m - 2) * k:
            cdfValue = 0
        else:
            if m == 2:
                cdfValue = chi2.cdf(t, df=1)
            elif m == 3:
                integral = quad(
                    lambda r: r * exp(-(r ** 2) / 2) * acos(sqrt(k) / r),
                    sqrt(k),
                    sqrt(t),
                )[0]
                cdfValue = 2 / (pi * pDegInc) * integral
            elif m == 4:
                integral = quad(
                    lambda r: r ** 2
                    * exp(-(r ** 2) / 2)
                    * (
                        atan(sqrt(r ** 2 / k * (r ** 2 / k - 2)))
                        - sqrt(k) / r * atan(sqrt(r ** 2 / k - 2))
                        - sqrt(k) / r * acos((r ** 2 / k - 1) ** (-1 / 2))
                    ),
                    sqrt(2 * k),
                    sqrt(t),
                )[0]
                cdfValue = (2 / pi) ** (3 / 2) * (pDegInc) ** (-2) * integral
    else:
        raise ValueError("devel argument must be either `internal` or `external`")
    if cdfValue < -0.001 or cdfValue > 1.001:
        raise ValueError("cdfValue outside [0,1].")
    elif -0.001 <= cdfValue < 0:
        return 0
    elif 1 < cdfValue <= 1.001:
        return 1
    else:
        return cdfValue


if __name__ == "__main__":
    import time

    algorithm_args = [
        "-x",
        "probGiViTI_2017_Complessiva",
        "-y",
        "hospOutcomeLatest_RIC10",
        "-devel",
        "external",
        "-max_deg",
        "4",
        "-confLevels",
        "0.80, 0.95",
        "-thres",
        "0.95",
        "-num_points",
        "200",
        "-pathology",
        "dementia",
        "-dataset",
        "cb_data",
        "-filter",
        "",
        "-formula",
        "",
    ]
    runner = create_runner(
        for_class="CalibrationBelt",
        found_in="CALIBRATION_BELT/calibration_belt",
        alg_type="iterative",
        num_workers=1,
        algorithm_args=algorithm_args,
    )
    start = time.time()
    runner.run()
    end = time.time()
    print("Completed in ", end - start)
