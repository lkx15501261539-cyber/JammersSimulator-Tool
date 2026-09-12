"""Layered, resolution-independent mission animation. Rendering is observer-only."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF, QEvent, Signal
from PySide6.QtGui import (QColor, QPen, QBrush, QPainter, QPainterPath, QPolygonF,
                          QLinearGradient, QRadialGradient, QFont)
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsItem
from .replay import project
from .robot_art import paint_robot, paint_beacon
from .source_status import SOURCE_COLORS, SOURCE_LABELS, source_status

TEAL = '#187b83'
AMBER = '#af681f'
VIOLET = '#8261c5'
INK = '#244353'
MUTED = '#718692'
UNVISITED = '#4a79b9'
VISITED = '#21886f'
STATES = {'moving':'路径行进','measuring':'无线电测向','switching':'切换接收频道',
          'clear':'光学确认 / 清除','mission ended':'本局任务结束','idle':'等待任务开始'}


def pen(color, width=1, style=None):
    p=QPen(QColor(color),width)
    p.setCapStyle(Qt.PenCapStyle.RoundCap);p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if style is not None:p.setStyle(style)
    return p


def action_range(indices):
    ranges=[];first=last=indices[0]
    for i in indices[1:]:
        if i==last+1:last=i
        else:ranges.append(str(first) if first==last else f'{first}–{last}');first=last=i
    ranges.append(str(first) if first==last else f'{first}–{last}')
    text=', '.join(ranges)
    return text if len(text)<=28 else f'{indices[0]}…{indices[-1]} ({len(indices)} 次)'


def text(p,x,y,words,size=11,color=MUTED,bold=False):
    font=QFont('Arial',size);font.setBold(bold);p.setFont(font);p.setPen(QColor(color))
    p.drawText(QPointF(x,y),str(words))


def rounded(p,rect,fill,border='#d4e0e6',radius=12):
    p.setPen(pen(border));p.setBrush(QColor(fill));p.drawRoundedRect(rect,radius,radius)


def source_heading(orientation):
    """Source-to-receiver heading in screen coordinates: east 0°, north 90°."""
    angle=math.radians(float(orientation)%360)
    return QPointF(math.cos(angle),-math.sin(angle))


def directional_range_path(center,radius,orientation):
    """The forward 180° reception sector, using Qt's counterclockwise arcs."""
    path=QPainterPath(center)
    rect=QRectF(center.x()-radius,center.y()-radius,2*radius,2*radius)
    path.arcTo(rect,float(orientation)%360-90,180)
    path.closeSubpath()
    return path


class MapView(QGraphicsView):
    zoom_changed=Signal(float)
    follow_changed=Signal(bool)
    MIN_ZOOM=.5
    MAX_ZOOM=16.

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.TextAntialiasing)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QColor('#f5f8fa'))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.arena_view_rect=QRectF(-2200,-2200,4400,4400)
        self.setSceneRect(self.arena_view_rect)
        self.state=project([],0);self.sources=[];self.truth=True;self.radii=False
        self.show_annotations=False;self.show_closeup=True;self.follow_robot=False
        self.fitted=False;self.label_signature=None;self.metadata={}
        self._fit_scale=1.;self._overview=True;self._camera_center=QPointF()
        self.setMinimumSize(450,380)

    def showEvent(self,event):
        super().showEvent(event)
        if not self.fitted:self.reset_view()

    def resizeEvent(self,event):
        factor=self.zoom_factor() if self.fitted else 1.
        center=QPointF(self._camera_center)
        super().resizeEvent(event)
        if self.fitted:
            if self._overview and not self.follow_robot:self.reset_view()
            else:
                self._fit_scale=self._arena_fit_scale()
                ratio=self._fit_scale*factor/max(abs(self.transform().m11()),1e-12)
                self.scale(ratio,ratio)
                if self.follow_robot:self._center_follow_camera()
                else:self._center_camera(center)
                self.zoom_changed.emit(self.zoom_factor())

    def wheelEvent(self,event):
        pixels,angles=event.pixelDelta(),event.angleDelta()
        if pixels.isNull() and angles.isNull():
            event.accept();return
        modified=bool(event.modifiers() & (Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.MetaModifier))
        touchpad=(not pixels.isNull() or event.phase()!=Qt.ScrollPhase.NoScrollPhase
                  or event.pointingDevice().type().name=='TouchPad')
        if not modified and (touchpad or angles.y()==0):
            delta=QPointF(pixels) if not pixels.isNull() else QPointF(angles)/3.
            self._pan_pixels(delta)
        else:
            exponent=pixels.y()*.0035 if not pixels.isNull() else angles.y()*math.log(1.15)/120.
            if exponent:
                self.set_zoom(self.zoom_factor()*math.exp(max(-.18,min(.18,exponent))),event.position())
        event.accept()

    def event(self,event):
        if event.type()==QEvent.Type.NativeGesture and self._native_gesture(event):return True
        return super().event(event)

    def viewportEvent(self,event):
        if event.type()==QEvent.Type.NativeGesture and self._native_gesture(event):return True
        return super().viewportEvent(event)

    def _native_gesture(self,event):
        kind=event.gestureType()
        if kind==Qt.NativeGestureType.ZoomNativeGesture:
            value=event.value()
            if math.isfinite(value):
                self.set_zoom(self.zoom_factor()*(1+max(-.2,min(.25,value))),event.position())
        elif kind==Qt.NativeGestureType.PanNativeGesture:
            self._pan_pixels(event.delta())
        elif kind==Qt.NativeGestureType.SmartZoomNativeGesture:
            if self.zoom_factor()>1.05:self.reset_view()
            else:self.set_zoom(2.5,event.position())
        elif kind not in (Qt.NativeGestureType.BeginNativeGesture,Qt.NativeGestureType.EndNativeGesture,
                          Qt.NativeGestureType.RotateNativeGesture):return False
        event.accept();return True

    def _arena_fit_scale(self):
        return max(1e-6,min(max(1,self.viewport().width()-4)/self.arena_view_rect.width(),
                            max(1,self.viewport().height()-4)/self.arena_view_rect.height()))

    def _scene_point(self,viewport_point):
        inverse,_=self.viewportTransform().inverted()
        return inverse.map(QPointF(viewport_point))

    def _viewport_center(self):
        # QRect.center() rounds down; feeding that into centerOn introduces a
        # one-pixel bias on every high-frequency touchpad event.
        return QPointF(self.viewport().width()/2.,self.viewport().height()/2.)

    def zoom_factor(self):
        """Zoom relative to a fitted arena: 1.0 is 100 percent."""
        return abs(self.transform().m11())/self._fit_scale

    def set_zoom(self,factor,anchor=None):
        """Set bounded relative zoom, preserving the cursor or viewport center."""
        factor=float(factor)
        if not math.isfinite(factor) or factor<=0:raise ValueError('Zoom must be positive and finite')
        factor=max(self.MIN_ZOOM,min(self.MAX_ZOOM,factor))
        current=abs(self.transform().m11());target=self._fit_scale*factor
        if math.isclose(current,target,rel_tol=1e-12):
            self.zoom_changed.emit(factor);return factor
        center_pixel=self._viewport_center()
        anchor_pixel=center_pixel if anchor is None else QPointF(anchor)
        scene_anchor=self._camera_center+(anchor_pixel-center_pixel)/current
        self._overview=False
        self.scale(target/current,target/current)
        if self.follow_robot:self._center_follow_camera()
        else:self._center_camera(scene_anchor+(center_pixel-anchor_pixel)/target)
        self.viewport().update();self.zoom_changed.emit(self.zoom_factor())
        return self.zoom_factor()

    def zoom_in(self):self.set_zoom(self.zoom_factor()*1.25)

    def zoom_out(self):self.set_zoom(self.zoom_factor()/1.25)

    def _leave_follow(self):
        if self.follow_robot:
            self.follow_robot=False;self.follow_changed.emit(False)

    def _pan_pixels(self,delta):
        delta=QPointF(delta)
        if not math.isfinite(delta.x()) or not math.isfinite(delta.y()):return
        distance=math.hypot(delta.x(),delta.y())
        if not distance:return
        if distance>160:delta*=160/distance
        self._leave_follow();self._overview=False
        self._center_camera(self._camera_center-delta/max(abs(self.transform().m11()),1e-12))
        self.viewport().update()

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self._leave_follow();self._overview=False
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        super().mouseMoveEvent(event)
        if event.buttons() & Qt.MouseButton.LeftButton and not self.follow_robot:
            self._center_camera(self._scene_point(self._viewport_center()))

    def reset_view(self):
        was_following=self.follow_robot
        self.follow_robot=False
        self.setSceneRect(self.arena_view_rect)
        self.fitInView(self.arena_view_rect,Qt.AspectRatioMode.KeepAspectRatio)
        self._fit_scale=abs(self.transform().m11());self._overview=True;self.fitted=True
        self._camera_center=self._scene_point(self._viewport_center())
        self.viewport().update()
        if was_following:self.follow_changed.emit(False)
        self.zoom_changed.emit(1.)

    def set_follow(self,enabled):
        enabled=bool(enabled)
        if enabled==self.follow_robot:return
        if enabled:
            self.follow_robot=True
            self.set_zoom(self.arena_view_rect.height()/1160.)
            self._center_follow_camera();self.follow_changed.emit(True)
        else:self.reset_view()

    def _center_follow_camera(self):
        x,y=self.state['position'];self._center_camera(QPointF(x,-y))

    def _center_camera(self,center):
        # Keep the desired center in floating-point scene coordinates. Qt's
        # integer scrollbars must not discard fractional movement between ticks.
        self._camera_center=QPointF(center)
        visible=self.mapToScene(self.viewport().rect()).boundingRect()
        width,height=max(1.,visible.width()),max(1.,visible.height())
        margin=max(width,height)*.1+10
        camera_rect=QRectF(center.x()-width/2-margin,center.y()-height/2-margin,
                          width+2*margin,height+2*margin)
        # Recompute from the current view, rather than retaining every visited
        # extent. This is only a scrollable coordinate range, not a bitmap.
        self.setSceneRect(self.arena_view_rect.united(camera_rect))
        self.centerOn(center)

    def set_annotations(self,enabled):
        self.show_annotations=enabled
        for item in self.scene().items():item.setVisible(enabled)
        self.viewport().update()

    def set_closeup(self,enabled):
        self.show_closeup=enabled;self.viewport().update()

    def point(self,xy):
        return QPointF(self.mapFromScene(QPointF(xy[0],-xy[1])))

    def draw(self,state,sources,truth,radii):
        self.state,self.sources,self.truth,self.radii=state,sources,truth,radii
        if self.follow_robot:self._center_follow_camera()
        signature=(len(state['actions']),tuple((d['channel'],tuple(d['position'])) for d in state['actions']))
        if self.label_signature!=signature:
            self.label_signature=signature;self._labels()
        self.viewport().update()

    def _labels(self):
        self.scene().clear()
        groups={}
        for index,action in enumerate(self.state['actions'],1):
            groups.setdefault(tuple(action['position']),[]).append((index,action))
        for position,actions in groups.items():
            label=self.scene().addText(action_range([i for i,_ in actions]),QFont('Arial',9))
            label.setDefaultTextColor(QColor('#517486'))
            label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
            label.setPos(position[0],-position[1]);label.setZValue(5)
            label.setVisible(self.show_annotations)
            details=[f'位置 ({position[0]:.3f}, {position[1]:.3f}) m']
            for i,d in actions:
                angle=f" · 示向 {d['svd_deg']:.2f}°" if d.get('svd_deg') is not None else ''
                details.append(f"#{i} · 频道 {d['channel']} · {d['result']}{angle}")
            label.setToolTip('\n'.join(details))

    def drawBackground(self,painter,rect):
        p=painter;p.save();p.resetTransform()
        w,h=self.viewport().width(),self.viewport().height()
        p.fillRect(QRectF(0,0,w,h),QColor('#f5f8fa'))
        self._terrain(p,w,h)
        self._route(p)
        self._geometry(p)
        self._trail(p)
        self._sources(p)
        self._strategy_targets(p)
        p.restore()

    def drawForeground(self,painter,rect):
        p=painter;p.save();p.resetTransform()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=self.viewport().width(),self.viewport().height()
        self._point_markers(p)
        self._robot(p)
        self._hud(p,w,h)
        if self.show_closeup and w>=850 and h>=420:self._closeup(p,w,h)
        p.restore()

    def _terrain(self,p,w,h):
        center=self.point((0,0));scale=abs(self.transform().m11());radius=1800*scale
        arena=QRectF(center.x()-radius,center.y()-radius,2*radius,2*radius)
        p.setBrush(QColor('#ffffff'));p.setPen(pen('#aebfc9',1.2));p.drawEllipse(arena)
        clip=QPainterPath();clip.addEllipse(arena);p.save();p.setClipPath(clip)
        # A restrained coordinate grid; the arena contains no implied terrain.
        step=250 if scale<.7 else 100
        for v in range(-2000,2001,step):
            major=v%500==0
            p.setPen(pen('#e2e9ed' if major else '#f0f3f5',.7))
            p.drawLine(self.point((v,-2000)),self.point((v,2000)))
            p.drawLine(self.point((-2000,v)),self.point((2000,v)))
        p.restore()
        for deg in range(0,360,30):
            a=math.radians(deg);outer=radius+(7 if deg%30==0 else 3)
            p.setPen(pen('#a0b4bf',1))
            p.drawLine(QPointF(center.x()+radius*math.cos(a),center.y()+radius*math.sin(a)),
                       QPointF(center.x()+outer*math.cos(a),center.y()+outer*math.sin(a)))
        # Origin launch dock, rendered as a landing pad instead of a point.
        p.save();p.translate(center)
        p.setPen(pen('#7897a8'));p.setBrush(QColor('#eaf1f5'));p.drawRoundedRect(QRectF(-12,-12,24,24),4,4)
        p.setPen(pen('#45697b',1.4));p.drawLine(-6,0,6,0);p.drawLine(0,-6,0,6);p.restore()
        if not self.follow_robot:text(p,center.x()+17,center.y()+4,'起点',8,'#638091')

    def _trail(self,p):
        s=self.state
        path=QPainterPath(self.point((0,0)))
        for xy in s['trajectory']:path.lineTo(self.point(xy))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen(QColor(32,133,137,190),1.9));p.drawPath(path)
        event=s.get('active_event')
        if event and event['type']=='Move':
            target=event['data']['destination'];a=self.point(s['position']);b=self.point(target)
            p.setPen(pen(QColor(84,111,168,175),1.2,Qt.PenStyle.DashLine));p.drawLine(a,b)
            p.setPen(pen('#5673ac',1.2));p.drawLine(b+QPointF(-5,0),b+QPointF(5,0));p.drawLine(b+QPointF(0,-5),b+QPointF(0,5))
        if self.show_annotations:
            p.setBrush(QColor('#438c92'));p.setPen(Qt.PenStyle.NoPen)
            for action in s['actions']:p.drawEllipse(self.point(action['position']),2.3,2.3)

    def _route(self,p):
        """Draw only the fixed route published by the strategy observer.

        Visited flags are projected from executed motion, never from future
        events or from the full route's presence in a log.
        """
        route=self.state.get('route_points') or self.state.get('strategy',{}).get('route_points',[])
        if not route:return
        points=[self.point(item['position']) for item in route]
        path=QPainterPath(points[0])
        for point in points[1:]:path.lineTo(point)
        p.setPen(pen(QColor(107,134,171,105),1.15,Qt.PenStyle.DotLine))
        p.setBrush(Qt.BrushStyle.NoBrush);p.drawPath(path)

    def _point_markers(self,p):
        """One centered marker per coordinate, with its name inside.

        A fixed stop can also be a direction origin. Combine their names so
        the observer never implies an additional, displaced observation.
        """
        route=self.state.get('route_points') or self.state.get('strategy',{}).get('route_points',[])
        markers={}
        for item in route:
            xy=tuple(item['position'])
            marker=markers.setdefault(xy,{'labels':[], 'color':UNVISITED})
            marker['labels'].append(f"P{item.get('index',0)+1}")
            marker['fixed']=True
            marker['color']='#6b9582' if item.get('visited') else UNVISITED
        directions=[d for d in self.state['measurements'] if d['channel']==self.state['channel']]
        for index,direction in enumerate(directions,1):
            marker=markers.setdefault(tuple(direction['position']),{'labels':[], 'color':'#557ea3'})
            marker['labels'].append(f'D{index}')
        font=QFont('Arial',7);font.setBold(True);p.setFont(font)
        for xy,marker in markers.items():
            anchor=self.point(xy);label=' / '.join(marker['labels'])
            width=max(20,p.fontMetrics().horizontalAdvance(label)+10)
            badge=QRectF(anchor.x()-width/2,anchor.y()-10,width,20)
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(marker['color']))
            radius=3 if marker.get('fixed') else 10
            p.drawRoundedRect(badge,radius,radius)
            p.setPen(QColor('#ffffff'));p.drawText(badge,Qt.AlignmentFlag.AlignCenter,label)

    def _strategy_targets(self,p):
        context=self.state.get('strategy',{})
        target=context.get('current_target')
        if target and target.get('position') is not None:
            point=self.point(target['position'])
            p.setPen(pen('#436caa',1.4));p.setBrush(QColor(71,109,170,16))
            p.drawEllipse(point,12,12)
            for dx,dy in ((-1,0),(1,0),(0,-1),(0,1)):
                p.drawLine(point+QPointF(dx*9,dy*9),point+QPointF(dx*16,dy*16))
        # Unselected candidate proposals remain in observer logs. The map
        # shows committed targets only, so proposals cannot look like stops.
        if context.get('queue_kind')=='tail':
            pending=[task for task in context.get('tasks',[]) if task.get('position') is not None]
            for index,task in enumerate(pending,1):
                if target and task['position']==target.get('position'):continue
                point=self.point(task['position'])
                p.setPen(pen('#8695b5',1,Qt.PenStyle.DotLine));p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(point,8,8)
                if self.show_annotations:text(p,point.x()+11,point.y()+3,f'T{index}',8,'#6f7c99')

    def _sources(self,p):
        s=self.state;scale=abs(self.transform().m11())
        for source in self.sources:
            # Reception areas, source types and orientations are all truth.
            # The independent radii toggle must not reveal a hidden source.
            if not self.truth:continue
            center=self.point((source['x'],source['y']));ch=source['channel']
            status=source_status(s,ch);color=SOURCE_COLORS[status];cleared=status=='cleared'
            directional=source.get('source_type')=='directional'
            if self.radii:
                radius=source['recv_radius']*scale;outline=QColor(color);outline.setAlpha(40)
                p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(pen(outline,1,Qt.PenStyle.DashLine))
                if directional:p.drawPath(directional_range_path(center,radius,source['orientation']))
                else:p.drawEllipse(center,radius,radius)
            if self.truth:
                # A breathing halo identifies the actual selected task without
                # displacing the source's ground anchor or changing playback.
                if status=='target':
                    pulse=(1+math.sin(s['time']*2.4))/2
                    halo=QColor(color);halo.setAlpha(round(90+75*pulse))
                    fill=QColor(color);fill.setAlpha(round(12+12*pulse))
                    p.setPen(pen(halo,1.7));p.setBrush(fill)
                    p.drawEllipse(center,17+5*pulse,12+4*pulse)
                fill=QColor(color);fill.setAlpha(35)
                p.setPen(pen(color,1.2));p.setBrush(fill)
                p.drawEllipse(center,12 if self.follow_robot else 9,6 if self.follow_robot else 5)
                paint_beacon(p,center,ch,cleared,pulse=s['time']*.8,
                             scale=.68 if not self.follow_robot else .9,status=status)
                if directional:
                    heading=source_heading(source['orientation']);normal=QPointF(-heading.y(),heading.x())
                    tip=center+heading*(35 if self.follow_robot else 29)
                    p.setPen(pen(color,1.8));p.setBrush(Qt.BrushStyle.NoBrush)
                    p.drawLine(center+heading*12,tip)
                    p.drawLine(tip,tip-heading*7+normal*4)
                    p.drawLine(tip,tip-heading*7-normal*4)
                if self.show_annotations or ch==s['channel'] or status=='target':
                    label=f'CH {ch:02}'+(' · 目标' if status=='target' else '')
                    if directional:label+=f" · 定向 {source['orientation']%360:.0f}°"
                    text(p,center.x()+13,center.y()-12,label,9,color,status=='target')
        # Clear radius and outcome are observational events, independent of truth toggle.
        for clear in s['clear_actions']:
            pos=self.point(clear['position']);radius=20*scale
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(pen(QColor('#339974' if clear['result']=='success' else '#c75762'),1.5));p.drawEllipse(pos,radius,radius)
        anim=s.get('animation',{});latest=anim.get('latest_clear');stamp=anim.get('last_clear_time')
        if latest and stamp is not None:
            age=s['time']-stamp
            if 0<=age<8:
                center=self.point(latest['position']);color=QColor('#329878' if latest['result']=='success' else '#c75762')
                color.setAlpha(round(160*(1-age/8)));p.setPen(pen(color,2));p.setBrush(Qt.BrushStyle.NoBrush);p.drawEllipse(center,10+age*6,10+age*6)
        if 'active_clear' in s:
            center=self.point(s['position']);radius=20*scale
            p.setPen(pen(AMBER,1.7,Qt.PenStyle.DashLine));p.setBrush(QColor(214,151,62,25));p.drawEllipse(center,radius,radius)

    def _geometry(self,p):
        s=self.state;scale=abs(self.transform().m11());channel=s['channel']
        for d in s['measurements']:
            if d['channel']!=channel:continue
            x,y=d['position'];a=math.radians(d['svd_deg'])
            apex=self.point((x,y));ends=[self.point((x+4000*math.cos(a+sign*math.pi/180),y+4000*math.sin(a+sign*math.pi/180))) for sign in (-1,1)]
            path=QPainterPath(apex);path.lineTo(ends[0]);path.lineTo(ends[1]);path.closeSubpath()
            grad=QLinearGradient(apex,(ends[0]+ends[1])*.5);grad.setColorAt(0,QColor(210,155,64,34));grad.setColorAt(1,QColor(210,155,64,0))
            p.setPen(pen(QColor(185,139,58,50),.7));p.setBrush(grad);p.drawPath(path)
            p.setPen(pen(QColor(160,116,48,135),.85,Qt.PenStyle.DashLine));p.drawLine(apex,(ends[0]+ends[1])*.5)
        loc=s['localizations'].get(channel)
        if loc:
            vertices=[self.point(v) for v in loc['vertices']]
            if len(vertices)>=3:
                p.setPen(pen(VIOLET,1.6));p.setBrush(QColor(150,121,204,55));p.drawPolygon(QPolygonF(vertices))
            elif len(vertices)==2:p.setPen(pen(VIOLET,2));p.drawLine(*vertices)
            if loc.get('farthest_pair'):
                p.setPen(pen('#6e50aa',1.3));p.drawLine(*(self.point(v) for v in loc['farthest_pair']))
            if loc.get('center') is not None and loc.get('radius') is not None:
                center=self.point(loc['center']);r=loc['radius']*scale
                p.setPen(pen(QColor(130,98,185,155),1.2,Qt.PenStyle.DashLine));p.setBrush(Qt.BrushStyle.NoBrush);p.drawEllipse(center,r,r)
        for xy in s['candidates']:
            center=self.point(xy);p.setPen(pen('#5674a5'));p.setBrush(Qt.BrushStyle.NoBrush);p.drawRect(QRectF(center.x()-4,center.y()-4,8,8))
    def _robot(self,p):
        s=self.state;pos=self.point(s['position']);anim=s.get('animation',{})
        moving=s['status']=='moving';measuring=s['status']=='measuring'
        heading=-anim.get('heading_deg',0)
        if measuring:
            phase=anim.get('phase',0)
            for index in range(3):
                wave=(phase*2+index/3)%1;r=18+wave*62
                p.setPen(pen(QColor(35,143,151,round(100*(1-wave))),1.2));p.setBrush(Qt.BrushStyle.NoBrush);p.drawEllipse(pos,r,r)
        p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(pen(QColor(46,130,142,115),1))
        p.drawEllipse(pos,35 if self.follow_robot else 26,35 if self.follow_robot else 26)
        paint_robot(p,pos,heading_deg=heading,gait_phase=anim.get('phase',0)*math.tau if s['status']=='measuring' else s['distance']*abs(self.transform().m11())*math.tau/24,
                    moving=moving,measuring=measuring,scale=.7 if self.follow_robot else .52)
        # Crosshair marks the exact pose while the artwork uses a legible illustrative size.
        p.setPen(pen('#ecffff',1));p.drawLine(pos+QPointF(-3,0),pos+QPointF(3,0));p.drawLine(pos+QPointF(0,-3),pos+QPointF(0,3))

    def _hud(self,p,w,h):
        s=self.state;status=STATES.get(s['status'],s['status'])
        text(p,22,30,'探索地图',17,INK,True)
        text(p,22,51,'半径 1800 m  ·  实线为已执行轨迹',9,MUTED)
        color=AMBER if s['status'] in ('measuring','clear','switching') else TEAL
        chip=QRectF(22,64,174,28);rounded(p,chip,'#ffffff','#dce5ea',7)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(color));p.drawEllipse(QPointF(35,78),3,3)
        text(p,47,82,status,10,color,True)
        # A restrained coordinate frame makes map positions readable at any zoom.
        scale=abs(self.transform().m11())
        step=next((value for value in (100,200,500,1000,2000,5000,10000) if value*scale>=60),10000)
        visible=self.mapToScene(self.viewport().rect()).boundingRect()
        for value in range(math.floor(visible.left()/step)*step,math.ceil(visible.right()/step)*step+1,step):
            x=self.point((value,0)).x()
            if 50<x<w-100:
                text(p,x-13,h-7,str(value),8,'#8a9ca7')
        for value in range(math.floor(-visible.bottom()/step)*step,math.ceil(-visible.top()/step)*step+1,step):
            y=self.point((0,value)).y()
            lower_limit=h-295 if self.show_closeup and w>=850 and h>=420 else h-40
            if 120<y<lower_limit:text(p,9,y+3,str(value),8,'#8a9ca7')
        text(p,w-65,h-7,'X / m',8,'#6d8592')
        text(p,9,113,'Y / m',8,'#6d8592')
        cx=w-35;cy=35;p.setPen(pen('#73909f',1.2));p.drawLine(QPointF(cx,cy+12),QPointF(cx,cy-10))
        p.drawLine(QPointF(cx,cy-10),QPointF(cx-4,cy-4));p.drawLine(QPointF(cx,cy-10),QPointF(cx+4,cy-4))
        text(p,cx-4,cy-16,'N',9,INK,True)
        text(p,w-204,24,'跟随视角' if self.follow_robot else '全域视角',9,'#6e8693')
        text(p,w-204,42,'图标为示意尺寸',8,'#91a0a9')
        context=s.get('strategy',{});target=context.get('current_target')
        if target:
            action={'measure':'测向','clear':'清除'}.get(target.get('kind'),str(target.get('kind','')))
            channel=target.get('channel')
            label=f'当前目标 · {action}'+(f' CH {channel:02d}' if channel is not None else '')
            text(p,w-238,69,label,10,'#436caa',True)
        elif s['status']=='mission ended':
            text(p,w-238,69,f"任务结束 · 已清除 {len(s['cleared'])} / {len(self.sources)}",10,VISITED,True)
        if not self.truth:text(p,w-238,87,'源真值已隐藏',8,MUTED)
        # Consistent colors and shapes for source state and fixed route visits.
        legend=QRectF(w-262,h-165,240,107)
        rounded(p,legend,'#ffffff','#dee7eb',8)
        for index,status in enumerate(('unseen','detected','target','cleared')):
            x=legend.x()+14+(index%2)*112;y=legend.y()+20+(index//2)*20
            color=SOURCE_COLORS[status]
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(color));p.drawEllipse(QPointF(x+3,y),3,3)
            text(p,x+13,y+4,SOURCE_LABELS[status],9,'#5e7683')
        for x,label,color in ((legend.x()+14,'未访问路点',UNVISITED),(legend.x()+126,'已访问路点','#6b9582')):
            p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(color))
            p.drawRoundedRect(QRectF(x,legend.y()+59,7,7),1.5,1.5)
            text(p,x+13,legend.y()+66,label,9,'#5e7683')
        p.setPen(pen('#21868a',1.8));p.drawLine(QPointF(legend.x()+14,legend.y()+87),QPointF(legend.x()+36,legend.y()+87))
        text(p,legend.x()+43,legend.y()+91,'实际轨迹',8,MUTED)
        p.setPen(pen('#8aa0bb',1.1,Qt.PenStyle.DotLine));p.drawLine(QPointF(legend.x()+124,legend.y()+87),QPointF(legend.x()+146,legend.y()+87))
        text(p,legend.x()+153,legend.y()+91,'固定路线',8,MUTED)
        meters=500 if scale<.3 else 100;length=meters*scale
        p.setPen(pen('#8a9fa9',1));p.drawLine(QPointF(w-22-length,h-29),QPointF(w-22,h-29))
        p.drawLine(QPointF(w-22-length,h-33),QPointF(w-22-length,h-25));p.drawLine(QPointF(w-22,h-33),QPointF(w-22,h-25))
        text(p,w-22-length,h-39,f'{meters} m',8,'#7a909c')

    def _closeup(self,p,w,h):
        s=self.state;anim=s.get('animation',{});box=QRectF(20,h-282,296,260)
        p.save();shadow=box.translated(0,3);rounded(p,shadow,'#e6edf2','#e6edf2',12)
        rounded(p,box,'#ffffff','#d4e0e7',12)
        text(p,box.x()+16,box.y()+23,'ROBOT VIEW',8,'#91a3ae',True)
        text(p,box.x()+16,box.y()+44,'机器狗近景',13,INK,True)
        text(p,box.right()-91,box.y()+23,f"CH {s['channel']:02d}",10,TEAL,True)
        # Closeup floor uses robot motion for a moving ground cue, never alters its pose.
        # Allow the full rotated gait envelope, including raised feet. The
        # 146 px floor leaves margin around the 1.3x robot at every gait phase.
        floor=QRectF(box.x()+10,box.y()+53,box.width()-20,146)
        p.save();p.setClipRect(floor)
        p.fillRect(floor,QColor('#f5f8fa'))
        shift=(s['distance']*.1)%24
        p.setPen(pen('#e6edf1',1))
        for i in range(-2,16):
            x=floor.x()+i*24-shift;p.drawLine(QPointF(x,floor.top()),QPointF(x,floor.bottom()))
        for i in range(8):
            y=floor.y()+i*24;p.drawLine(QPointF(floor.left(),y),QPointF(floor.right(),y))
        center=QPointF(box.center().x(),box.y()+126)
        if s['status']=='measuring':
            phase=anim.get('phase',0);r=31+phase*34
            p.setBrush(QColor(45,148,149,8));p.setPen(pen(QColor(48,150,149,100),1));p.drawEllipse(center,r,r*.58)
            a=phase*math.tau*2;end=center+QPointF(math.cos(a)*92,math.sin(a)*44)
            p.setPen(pen('#379da0',1.7));p.drawLine(center,end)
        elif s['status']=='clear':
            phase=anim.get('phase',0);p.setPen(pen(AMBER,1,Qt.PenStyle.DashLine));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center,85,49)
            for sign in (-1,1):
                x=center.x()+sign*72;p.drawLine(QPointF(x,center.y()-25),QPointF(x,center.y()+25))
            a=(phase-.5)*.9
            p.setPen(pen(QColor(189,128,42,170),1.7));p.drawLine(center+QPointF(45,0),center+QPointF(118,math.sin(a)*50))
        paint_robot(p,center,heading_deg=-18,gait_phase=anim.get('phase',0)*math.tau if s['status']=='measuring' else s['distance']*abs(self.transform().m11())*math.tau/24,
                    moving=s['status']=='moving',measuring=s['status']=='measuring',scale=1.3)
        p.restore()
        state_label=STATES.get(s['status'],s['status'])
        if s['status']=='moving':state_label='四足行进  ·  5.0 m/s'
        if s['status']=='measuring':state_label='天线扫描  ·  测向采集 5 s'
        if s['status']=='switching':state_label='接收机调谐  ·  切频 1 s'
        if s['status']=='clear':state_label='20 m 光学搜索与清除'
        text(p,box.x()+16,box.y()+220,state_label,10,INK,True)
        track=QRectF(box.x()+16,box.y()+232,box.width()-32,4)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#e3edf0'));p.drawRoundedRect(track,2,2)
        phase=anim.get('phase',0)
        if phase:p.setBrush(QColor(AMBER if s['status']!='moving' else TEAL));p.drawRoundedRect(QRectF(track.x(),track.y(),track.width()*phase,4),2,2)
        text(p,box.x()+16,box.bottom()-9,f"X {s['position'][0]:.1f}    Y {s['position'][1]:.1f} m",8,'#7c9dac')
        p.restore()
