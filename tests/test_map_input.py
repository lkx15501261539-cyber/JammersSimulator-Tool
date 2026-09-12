"""Real Qt input events exercise touchpad, mouse wheel, and native gestures."""
import math
import os

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import Qt,QPoint,QPointF
from PySide6.QtGui import QWheelEvent,QNativeGestureEvent,QPointingDevice,QInputDevice
from PySide6.QtWidgets import QApplication
from enhanced.map_view import MapView
from enhanced.replay import project


@pytest.fixture
def canvas():
    app=QApplication.instance() or QApplication([])
    view=MapView();view.resize(920,620);view.show();app.processEvents()
    device=QPointingDevice('test touchpad',123,QInputDevice.DeviceType.TouchPad,
                           QPointingDevice.PointerType.Finger,QInputDevice.Capability.Position,5,0)
    yield app,view,device
    view.close();view.deleteLater();app.processEvents()


def wheel(view,pixels=(0,0),angles=(0,0),modifiers=Qt.KeyboardModifier.NoModifier,
          phase=Qt.ScrollPhase.NoScrollPhase,position=(730,190),device=None):
    point=QPointF(*position)
    event=QWheelEvent(point,QPointF(view.viewport().mapToGlobal(point.toPoint())),
                      QPoint(*pixels),QPoint(*angles),Qt.MouseButton.NoButton,modifiers,phase,False,
                      Qt.MouseEventSource.MouseEventNotSynthesized,
                      device or QPointingDevice.primaryPointingDevice())
    QApplication.sendEvent(view.viewport(),event)
    assert event.isAccepted()


def native(view,device,kind,value=0.,delta=(0.,0.),position=(730,190),viewport=True):
    point=QPointF(*position)
    event=QNativeGestureEvent(kind,device,2,point,point,point,value,QPointF(*delta))
    QApplication.sendEvent(view.viewport() if viewport else view,event)
    assert event.isAccepted()


def scene_point(view,position):
    inverse,_=view.viewportTransform().inverted()
    return inverse.map(QPointF(*position))


def test_zero_scroll_begin_and_end_do_not_zoom_out(canvas):
    _,view,_=canvas
    for phase in (Qt.ScrollPhase.ScrollBegin,Qt.ScrollPhase.ScrollEnd):wheel(view,phase=phase)
    assert view.zoom_factor()==pytest.approx(1.)


@pytest.mark.parametrize('modifier',[Qt.KeyboardModifier.ControlModifier,Qt.KeyboardModifier.MetaModifier])
def test_modified_pixel_scroll_is_smooth_and_prefers_pixel_delta(canvas,modifier):
    _,view,_=canvas
    for _ in range(20):wheel(view,pixels=(0,1),angles=(0,120),modifiers=modifier)
    assert view.zoom_factor()==pytest.approx(math.exp(20*.0035))
    for _ in range(20):wheel(view,pixels=(0,-1),angles=(0,-120),modifiers=modifier)
    assert view.zoom_factor()==pytest.approx(1.)


def test_regular_mouse_wheel_uses_fractional_angle_delta_and_cursor_anchor(canvas):
    _,view,_=canvas
    anchor=scene_point(view,(730,190))
    wheel(view,angles=(0,15))
    assert view.zoom_factor()==pytest.approx(1.15**(15/120))
    projected=view.viewportTransform().map(anchor)
    assert projected.x()==pytest.approx(730,abs=1.5)
    assert projected.y()==pytest.approx(190,abs=1.5)


@pytest.mark.parametrize('pixels',[(8,15),(12,0),(0,-13)])
def test_plain_two_finger_scroll_pans_without_changing_zoom(canvas,pixels):
    _,view,device=canvas
    origin_before=view.point((0,0))
    wheel(view,pixels=pixels,angles=(0,120),phase=Qt.ScrollPhase.ScrollUpdate,device=device)
    origin_after=view.point((0,0))
    assert view.zoom_factor()==pytest.approx(1.)
    assert origin_after.x()-origin_before.x()==pytest.approx(pixels[0],abs=1.5)
    assert origin_after.y()-origin_before.y()==pytest.approx(pixels[1],abs=1.5)


def test_touchpad_without_pixel_delta_uses_pan_fallback(canvas):
    _,view,device=canvas
    before=view.point((0,0))
    wheel(view,angles=(0,24),device=device)
    assert view.zoom_factor()==pytest.approx(1.)
    assert view.point((0,0)).y()-before.y()==pytest.approx(8,abs=1.5)


def test_large_wheel_steps_are_capped_and_zoom_bounds_hold(canvas):
    _,view,_=canvas
    wheel(view,angles=(0,120000))
    assert 1.<view.zoom_factor()<1.25
    for _ in range(40):wheel(view,angles=(0,120000))
    assert view.zoom_factor()==pytest.approx(view.MAX_ZOOM)
    for _ in range(40):wheel(view,angles=(0,-120000))
    assert view.zoom_factor()==pytest.approx(view.MIN_ZOOM)


@pytest.mark.parametrize('viewport',[True,False])
def test_interleaved_native_pinch_rotation_and_boundaries_are_not_double_applied(canvas,viewport):
    _,view,device=canvas
    native(view,device,Qt.NativeGestureType.BeginNativeGesture,viewport=viewport)
    native(view,device,Qt.NativeGestureType.ZoomNativeGesture,.1,viewport=viewport)
    native(view,device,Qt.NativeGestureType.RotateNativeGesture,90.,viewport=viewport)
    native(view,device,Qt.NativeGestureType.ZoomNativeGesture,-.05,viewport=viewport)
    native(view,device,Qt.NativeGestureType.EndNativeGesture,viewport=viewport)
    assert view.zoom_factor()==pytest.approx(1.1*.95)
    assert view.transform().m12()==0. and view.transform().m21()==0.


def test_native_pinch_rejects_nonfinite_and_limits_extreme_increment(canvas):
    _,view,device=canvas
    native(view,device,Qt.NativeGestureType.ZoomNativeGesture,float('nan'))
    assert view.zoom_factor()==pytest.approx(1.)
    native(view,device,Qt.NativeGestureType.ZoomNativeGesture,10000.)
    assert view.zoom_factor()==pytest.approx(1.25)
    native(view,device,Qt.NativeGestureType.ZoomNativeGesture,-10000.)
    assert view.zoom_factor()==pytest.approx(1.)


def test_pan_exits_follow_without_resetting_zoom_and_pinch_keeps_follow_centered(canvas):
    _,view,device=canvas
    state=project([],0);state['position']=[1750.,0.]
    view.draw(state,[],False,False)
    observed=[];view.follow_changed.connect(observed.append)
    # Emulates the UI checkbox feeding its synchronized state back to the map.
    view.follow_changed.connect(view.set_follow)
    view.set_follow(True)
    native(view,device,Qt.NativeGestureType.ZoomNativeGesture,.1)
    point=view.point(state['position']);center=view.viewport().rect().center()
    assert point.x()==pytest.approx(center.x(),abs=1.5)
    factor=view.zoom_factor()
    wheel(view,pixels=(0,12),phase=Qt.ScrollPhase.ScrollUpdate)
    assert not view.follow_robot and observed==[True,False]
    assert view.zoom_factor()==pytest.approx(factor)
    assert view.state==state


def test_zoom_buttons_limits_reset_and_signals(canvas):
    _,view,_=canvas
    observed=[];view.zoom_changed.connect(observed.append)
    view.zoom_in();assert view.zoom_factor()==pytest.approx(1.25)
    view.zoom_out();assert view.zoom_factor()==pytest.approx(1.)
    view.set_zoom(100.);assert view.zoom_factor()==pytest.approx(view.MAX_ZOOM)
    view.reset_view();assert view.zoom_factor()==pytest.approx(1.)
    assert observed[-1]==1.
    for invalid in (0.,-1.,float('nan'),float('inf')):
        with pytest.raises(ValueError):view.set_zoom(invalid)


def test_resize_preserves_manual_zoom_instead_of_resetting_overview(canvas):
    app,view,_=canvas
    view.set_zoom(2.75)
    wheel(view,pixels=(18,12),phase=Qt.ScrollPhase.ScrollUpdate)
    before=scene_point(view,(view.viewport().width()/2,view.viewport().height()/2))
    view.resize(1320,480);app.processEvents()
    assert view.zoom_factor()==pytest.approx(2.75)
    assert not view.follow_robot
    after=scene_point(view,(view.viewport().width()/2,view.viewport().height()/2))
    tolerance=1.5/abs(view.transform().m11())
    assert after.x()==pytest.approx(before.x(),abs=tolerance)
    assert after.y()==pytest.approx(before.y(),abs=tolerance)


def test_twenty_one_pixel_trackpad_deltas_move_twenty_pixels(canvas):
    _,view,_=canvas
    view.set_zoom(2.75)
    before=view.point((0,0))
    for _ in range(20):wheel(view,pixels=(1,1),phase=Qt.ScrollPhase.ScrollUpdate)
    after=view.point((0,0))
    assert after.x()-before.x()==pytest.approx(20,abs=1.)
    assert after.y()-before.y()==pytest.approx(20,abs=1.)
    for _ in range(20):wheel(view,pixels=(-1,-1),phase=Qt.ScrollPhase.ScrollUpdate)
    returned=view.point((0,0))
    assert returned.x()==pytest.approx(before.x(),abs=1.)
    assert returned.y()==pytest.approx(before.y(),abs=1.)


def test_fractional_native_pan_accumulates_without_rounding_loss(canvas):
    _,view,device=canvas
    before=view.point((0,0))
    for _ in range(20):native(view,device,Qt.NativeGestureType.PanNativeGesture,delta=(.25,.25))
    after=view.point((0,0))
    assert after.x()-before.x()==pytest.approx(5.,abs=1.)
    assert after.y()-before.y()==pytest.approx(5.,abs=1.)


def test_many_small_zoom_deltas_keep_original_cursor_anchor(canvas):
    _,view,_=canvas
    anchor=scene_point(view,(730,190))
    for _ in range(100):wheel(view,pixels=(0,1),modifiers=Qt.KeyboardModifier.ControlModifier)
    after=view.viewportTransform().map(anchor)
    assert after.x()==pytest.approx(730,abs=1.5)
    assert after.y()==pytest.approx(190,abs=1.5)
