"""Poisson Deviance scorer recipe."""
import numpy as np
import typing
from h2oaicore.metrics import CustomScorer
from h2oaicore.systemutils import make_experiment_logger, loggerinfo, loggerwarning


class PoissonDevianceScorer(CustomScorer):
    """
    Starting 1.10.2 - DAI handles exceptions raised by custom scorers.
    Default DAI behavior is to continue experiment in case of Scorer failure.
    To enable forcing experiment to fail, in case of scorer error, set following parameters in DAI:
      - skip_scorer_failures=false (Disabled)
      - skip_model_failures=false (Disabled)
    """
    _description = NotImplemented
    _maximize = False  # whether a higher score is better
    _perfect_score = 0.0  # the ideal score, used for early stopping once validation score achieves this value
    _supports_sample_weight = True  # whether the scorer accepts and uses the sample_weight input
    _regression = True
    _display_name = "Poisson_Deviance"

    @staticmethod
    def do_acceptance_test():
        """
        Whether to enable acceptance tests during upload of recipe and during start of Driverless AI.
        Acceptance tests perform a number of sanity checks on small data, and attempt to provide helpful instructions
        for how to fix any potential issues. Disable if your recipe requires specific data or won't work on random data.
        """
        return False

    @property
    def logger(self):
        from h2oaicore import application_context
        from h2oaicore.systemutils import exp_dir
        # Don't assign to self, not picklable
        return make_experiment_logger(experiment_id=application_context.context.experiment_id, tmp_dir=None,
                                      experiment_tmp_dir=exp_dir())

    def score(self, actual: np.array, predicted: np.array, sample_weight: typing.Optional[np.array] = None,
              labels: typing.Optional[np.array] = None) -> float:
        """Initialize logger to print additional info in case of invalid inputs(exception is raised) and to enable debug prints"""
        logger = self.logger
        from h2oaicore.systemutils import loggerinfo
        # loggerinfo(logger, "Start Poisson Deviance Scorer.......")
        # loggerinfo(logger, 'Actual:%s' % str(actual))
        # loggerinfo(logger, 'Predicted:%s' % str(predicted))
        # loggerinfo(logger, 'Sample W:%s' % str(sample_weight))

        try:
            if sample_weight is not None:
                '''Check if any element of the sample_weight array is nan'''
                if np.isnan(np.sum(sample_weight)):
                    loggerinfo(logger, 'Sample Weight:%s' % str(sample_weight))
                    loggerinfo(logger, 'Sample Weight Nan values index:%s' % str(np.argwhere(np.isnan(sample_weight))))
                    raise RuntimeError(
                        'Error during Poisson Deviance score calculation. Invalid sample weight values. Expecting only non-nan values')

            '''Cast as float to avoid error with addition when DAI sends integers'''
            actual = actual.astype('float64')
            predicted = predicted.astype('float64')
            '''Safety mechanizm in case predictions or actuals are zero'''
            epsilon = 1E-8
            actual += epsilon
            predicted += epsilon

            '''Check if any element of the arrays is not positive or nan'''
            if (actual <= 0).any() or np.isnan(np.sum(actual)):
                loggerinfo(logger, 'Actual:%s' % str(actual))
                loggerinfo(logger, 'Non-positive Actuals:%s' % str(actual[actual <= 0]))
                loggerinfo(logger, 'Nan values index:%s' % str(np.argwhere(np.isnan(actual))))
                raise RuntimeError(
                    'Error during Poisson deviance score calculation. Invalid actuals values. Expecting only positive values')
            if (predicted <= 0).any() or np.isnan(np.sum(predicted)):
                loggerinfo(logger, 'Predicted:%s' % str(predicted))
                loggerinfo(logger, 'Invalid Predicted:%s' % str(predicted[predicted <= 0]))
                loggerinfo(logger, 'Nan values index:%s' % str(np.argwhere(np.isnan(predicted))))
                raise RuntimeError(
                    'Error during Poisson deviance score calculation. Invalid predicted values. Expecting only positive values')

            dev = 2 * (actual * np.log(actual / predicted) + predicted - actual)
            score = np.average(dev, weights=sample_weight)
            '''Validate that score is non-negative and is not infinity or Nan'''
            if score >= 0 and score < float("inf"):
                pass
            else:
                loggerinfo(logger, 'Invalid calculated score:%s' % str(score))
                raise RuntimeError(
                    'Error during Poisson Deviance score calculation. Invalid calculated score:%s. \
                     Score should be non-negative and less than infinity. Nan is not valid' % str(score))

        except Exception as e:
            '''Print error message into DAI log file'''
            loggerinfo(logger, 'Error during Poisson Deviance score calculation. Exception raised: %s' % str(e))
            raise
        return score
