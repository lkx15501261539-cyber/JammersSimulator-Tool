"""Follow framing remains centered at map edges and outside the arena."""
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication
from enhanced.map_view import MapView
from enhanced.replay import project


@pytest.fixture
def camera():
    app=QApplication.instance() or QApplication([])
    view=MapView()
    view.resize(920,620)
    view.show()
    app.processEvents()
    yield app,view
    view.close()
    view.deleteLater()
    app.processEvents()


def move_observer(view,position):
    state=project([],0)
    state['position']=list(position)
    view.draw(state,[],False,False)


def assert_robot_centered(view):
    robot=view.point(view.state['position'])
    center=view.viewport().rect().center()
    assert robot.x()==pytest.approx(center.x(),abs=1.5)
    assert robot.y()==pytest.approx(center.y(),abs=1.5)


@pytest.mark.parametrize('position', [
    (1750.,0.),(-1750.,0.),(0.,1750.),(0.,-1750.),(1250.,1250.),
    (8000.,-6500.),(-25000.,18000.),
])
def test_follow_centers_border_and_outside_positions(camera,position):
    app,view=camera
    view.set_follow(True)
    move_observer(view,position)
    app.processEvents()
    assert view.follow_robot
    assert_robot_centered(view)


def test_enabling_follow_at_border_then_resizing_keeps_robot_centered(camera):
    app,view=camera
    move_observer(view,(1750.,0.))
    view.set_follow(True)
    assert_robot_centered(view)
    view.resize(1320,470)
    app.processEvents()
    assert view.follow_robot
    assert_robot_centered(view)


def test_reset_fits_original_arena_after_far_outside_follow(camera):
    app,view=camera
    view.set_follow(True)
    move_observer(view,(8000.,-6500.))
    view.reset_view()
    app.processEvents()
    assert not view.follow_robot
    assert view.sceneRect()==QRectF(-2200,-2200,4400,4400)
    for x,y in ((1800,0),(-1800,0),(0,1800),(0,-1800)):
        assert view.viewport().rect().contains(view.mapFromScene(x,y))


def test_follow_bounds_do_not_accumulate_prior_positions(camera):
    _,view=camera
    view.set_follow(True)
    for position in ((1000000.,0.),(-1000000.,0.),(0.,0.)):
        move_observer(view,position)
        assert_robot_centered(view)
    assert view.sceneRect()==QRectF(-2200,-2200,4400,4400)
