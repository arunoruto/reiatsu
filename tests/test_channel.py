import numpy as np
import pytest

from reiatsu.channel import ChannelParams, Likelihood, predict
from reiatsu.plan import load_plan

from .test_plan import TWO_ROOMS


def test_path_loss_and_wall_loss():
    plan = load_plan(TWO_ROOMS)
    params = ChannelParams({"a": -60.0}, 2.0, {"brick": 10.0})
    # receiver at (1.5, 1.5, z=1); points at z=1
    pts = np.array([[2.5, 1.5], [3.5, 1.5], [5.5, 1.5], [3.5, 0.5]])
    mu = predict(plan, params, pts)[0]
    d = np.linalg.norm(pts - [1.5, 1.5], axis=1)
    expected = -60 - 20 * np.log10(d) - np.array([0, 10, 10, 0])
    assert np.allclose(mu, expected)


def test_unknown_material_rejected():
    cfg = dict(TWO_ROOMS, walls=[{"rooms": ["A", "B"], "material": "unobtainium"}])
    with pytest.raises(ValueError):
        ChannelParams.prior(load_plan(cfg))


def test_likelihood_prefers_matching_cell_and_handles_not_heard():
    mu = np.array([[-50.0, -70.0, -98.0], [-90.0, -70.0, -50.0]])  # 2 receivers, 3 cells
    lik = Likelihood()
    assert np.argmax(lik(mu, np.array([-51.0, -89.0]))) == 0
    # receiver 0 hears nothing: the cell where it would be weakest wins
    assert np.argmax(lik(mu, np.array([np.nan, -52.0]))) == 2
    # a huge outlier on one receiver doesn't flip the answer (Student-t)
    assert np.argmax(lik(mu, np.array([-51.0, -40.0]))) == 0
