from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import pandas as pd
import scipy.stats

from mipframework import Algorithm
from mipframework import AlgorithmResult
from mipframework import TabularDataResource


class Anova(Algorithm):
    def __init__(self, cli_args):
        super(Anova, self).__init__(__file__, cli_args, intercept=False)

    def local_(self):
        X = self.data.full
        variable = self.parameters.y[0]
        covariable = self.parameters.x[0]
        covar_label = self.metadata.label[covariable]

        model = AdditiveAnovaModel(X, variable, covariable)

        self.push_and_add(model=model)
        self.push_and_agree(covar_label=covar_label)

    def global_(self):
        model = self.fetch("model")
        covar_label = self.fetch("covar_label")

        res = model.get_anova_table()

        table = TabularDataResource(
            fields=["", "df", "sum_sq", "mean_sq", "F", "Pr(>F)"],
            data=[
                [
                    covar_label,
                    res["df_explained"],
                    res["ss_explained"],
                    res["ms_explained"],
                    res["f_stat"],
                    res["p_value"],
                ],
                [
                    "Residual",
                    res["df_residual"],
                    res["ss_residual"],
                    res["ms_residual"],
                    None,
                    None,
                ],
            ],
            title="Anova Summary",
        )

        self.result = AlgorithmResult(
            raw_data={"anova_table": res,}, tables=[table], highcharts=[],
        )


class AdditiveAnovaModel(object):
    def __init__(self, X=None, variable=None, covariable=None):
        if X is not None and variable and covariable:
            self.variable = variable
            self.covariable = covariable
            self.var_sq = variable + "_sq"
            X[self.var_sq] = X[variable] ** 2

            self.n_obs = X.shape[0]

            self.overall_stats = self.get_overall_stats(X)

            self.group_stats = self.get_group_stats(X)

    def __add__(self, other):
        result = AdditiveAnovaModel()

        assert self.variable == other.variable, "variable names do not agree"
        result.variable = self.variable

        assert self.covariable == other.covariable, "covariable names do not agree"
        result.covariable = self.covariable

        result.n_obs = self.n_obs + other.n_obs

        result.overall_stats = self.overall_stats + other.overall_stats

        result.group_stats = self.group_stats.add(other.group_stats, fill_value=0)

        return result

    def get_overall_stats(self, X):
        variable = self.variable
        var_sq = self.var_sq
        overall_stats = X[variable].agg(["count", "sum"])
        overall_ssq = X[var_sq].sum()
        overall_stats = overall_stats.append(
            pd.Series(data=overall_ssq, index=["sum_sq"])
        )
        return overall_stats

    def get_group_stats(self, X):
        variable = self.variable
        covar = self.covariable
        group_stats = X[[variable, covar]].groupby(covar).agg(["count", "sum"])
        group_stats.columns = ["count", "sum"]
        return group_stats

    def get_df_explained(self):
        return len(self.group_stats) - 1

    def get_df_residual(self):
        return self.n_obs - len(self.group_stats)

    def get_ss_residual(self):
        overall_sum_sq = self.overall_stats["sum_sq"]
        group_sum = self.group_stats["sum"]
        group_count = self.group_stats["count"]
        return overall_sum_sq - sum(group_sum ** 2 / group_count)

    def get_ss_total(self):
        overall_sum_sq = self.overall_stats["sum_sq"]
        overall_sum = self.overall_stats["sum"]
        overall_count = self.overall_stats["count"]
        return overall_sum_sq - (overall_sum ** 2 / overall_count)

    def get_ss_explained(self):
        group_sum = self.group_stats["sum"]
        group_count = self.group_stats["count"]
        return sum((self.overall_mean - group_sum / group_count) ** 2 * group_count)

    def get_anova_table(self):
        df_explained = self.get_df_explained()
        df_residual = self.get_df_residual()
        ss_explained = self.get_ss_explained()
        ss_residual = self.get_ss_residual()
        ms_explained = ss_explained / df_explained
        ms_residual = ss_residual / df_residual
        f_stat = ms_explained / ms_residual
        p_value = 1 - scipy.stats.f.cdf(f_stat, df_explained, df_residual)
        return dict(
            df_explained=df_explained,
            df_residual=df_residual,
            ss_explained=ss_explained,
            ss_residual=ss_residual,
            ms_explained=ms_explained,
            ms_residual=ms_residual,
            f_stat=f_stat,
            p_value=p_value,
        )

    @property
    def overall_mean(self):
        return self.overall_stats["sum"] / self.overall_stats["count"]

    def to_dict(self):  # useful for debugging
        dd = {
            "variable": self.variable,
            "covariable": self.covariable,
            "n_obs": self.n_obs,
            "overall_stats": self.overall_stats.tolist(),
            "group_stats": self.group_stats.values.tolist(),
        }
        return dd


if __name__ == "__main__":
    import time
    from mipframework import create_runner

    algorithm_args = [
        "-y",
        "lefthippocampus",
        "-x",
        "ppmicategory",
        "-pathology",
        "dementia",
        "-dataset",
        "ppmi",
        "-filter",
        "",
    ]
    runner = create_runner(Anova, algorithm_args=algorithm_args, num_workers=1,)
    start = time.time()
    runner.run()
    end = time.time()
