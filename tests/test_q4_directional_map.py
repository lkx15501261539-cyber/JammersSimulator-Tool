"""Observer-only Q4 direction/range rendering, without launching a model."""
import os

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest

pytest.importorskip('PySide6')
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from enhanced.map_view import MapView, directional_range_path, source_heading
from enhanced.replay import project


@pytest.fixture
def view():
    app=QApplication.instance() or QApplication([])
    widget=MapView();widget.resize(640,640);widget.show();app.processEvents()
    yield widget
    widget.close();widget.deleteLater();app.processEvents()


def source(orientation=0.,source_type='directional'):
    return dict(channel=2,x=0.,y=0.,recv_radius=1000.,
                source_type=source_type,orientation=orientation)


def render_sources(view,sources,truth=True,radii=True):
    view.draw(project([],0),sources,truth,radii)
    image=QImage(view.viewport().size(),QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:view._sources(painter)
    finally:painter.end()
    return image


def ink_near(image,point,radius=3):
    x,y=round(point.x()),round(point.y())
    return any(image.pixelColor(i,j).alpha()>0
               for i in range(x-radius,x+radius+1)
               for j in range(y-radius,y+radius+1)
               if 0<=i<image.width() and 0<=j<image.height())


@pytest.mark.parametrize('orientation',[0.,45.,90.,179.,180.,225.,270.,-90.,450.])
def test_sector_contains_only_forward_half_plane(orientation):
    center=QPointF(160,160)
    sector=directional_range_path(center,100,orientation)
    for offset in (-89,0,89):
        assert sector.contains(center+source_heading(orientation+offset)*50)
    for offset in (-91,91,180):
        assert not sector.contains(center+source_heading(orientation+offset)*50)
    assert not sector.contains(center+source_heading(orientation)*101)


@pytest.mark.parametrize('orientation,expected',[(0,(1,0)),(90,(0,-1)),(180,(-1,0)),(270,(0,1))])
def test_heading_matches_world_east_north_convention(orientation,expected):
    heading=source_heading(orientation)
    assert heading.x()==pytest.approx(expected[0],abs=1e-12)
    assert heading.y()==pytest.approx(expected[1],abs=1e-12)


@pytest.mark.parametrize('orientation',[0.,90.,180.,270.,45.])
def test_actual_painter_draws_forward_range_and_heading_arrow(view,orientation):
    image=render_sources(view,[source(orientation)])
    center=view.point((0.,0.));heading=source_heading(orientation)
    radius=1000*abs(view.transform().m11())
    assert ink_near(image,center+heading*radius)
    assert not ink_near(image,center-heading*radius)
    # An arrow remains visible even when reception ranges are switched off.
    marker=render_sources(view,[source(orientation)],radii=False)
    assert ink_near(marker,center+heading*29)
    omni=render_sources(view,[source(orientation,'omnidirectional')],radii=False)
    assert marker!=omni


@pytest.mark.parametrize('radii',[False,True])
def test_truth_off_hides_type_heading_and_reception_area(view,radii):
    empty=render_sources(view,[],truth=False,radii=radii)
    for kind in ('directional','omnidirectional'):
        for orientation in (0.,90.,215.):
            assert render_sources(view,[source(orientation,kind)],truth=False,radii=radii)==empty


def test_omnidirectional_and_legacy_q3_sources_keep_full_circle(view):
    omni=source(215.,'omnidirectional')
    image=render_sources(view,[omni])
    center=view.point((0.,0.));radius=omni['recv_radius']*abs(view.transform().m11())
    for orientation in (0.,90.,180.,270.):
        assert ink_near(image,center+source_heading(orientation)*radius)
    legacy={key:value for key,value in omni.items() if key not in ('source_type','orientation')}
    assert render_sources(view,[legacy])==image


def test_truth_off_preserves_observed_clear_actions(view):
    state=project([],0)
    state['clear_actions']=[dict(position=[150.,200.],result='success')]
    view.draw(state,[source()],False,True)
    image=QImage(view.viewport().size(),QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter=QPainter(image)
    try:view._sources(painter)
    finally:painter.end()
    assert ink_near(image,view.point((150.,200.)),radius=5)
