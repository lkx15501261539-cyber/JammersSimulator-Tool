"""Layered, resolution-independent mission animation. Rendering is observer-only."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import (QColor, QPen, QBrush, QPainter, QPainterPath, QPolygonF,
                          QLinearGradient, QRadialGradient, QFont)
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsItem
from .replay import project
from .robot_art import paint_robot, paint_beacon

TEAL = '#64e0d1'
AMBER = '#f4bf72'
VIOLET = '#b6a1ff'
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


def text(p,x,y,words,size=11,color='#91a8b9',bold=False):
    font=QFont('Arial',size);font.setBold(bold);p.setFont(font);p.setPen(QColor(color))
    p.drawText(QPointF(x,y),str(words))


def rounded(p,rect,fill,border='#294354',radius=12):
    p.setPen(pen(border));p.setBrush(QColor(fill));p.drawRoundedRect(rect,radius,radius)


class MapView(QGraphicsView):
    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.TextAntialiasing)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QColor('#0b1722'))
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.arena_view_rect=QRectF(-2200,-2200,4400,4400)
        self.setSceneRect(self.arena_view_rect)
        self.state=project([],0);self.sources=[];self.truth=True;self.radii=False
        self.show_annotations=False;self.show_closeup=True;self.follow_robot=False
        self.fitted=False;self.label_signature=None;self.metadata={}
        self.setMinimumSize(450,380)

    def showEvent(self,event):
        super().showEvent(event)
        if not self.fitted:self.reset_view()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.fitted:
            if self.follow_robot:self._center_follow_camera()
            else:self.reset_view()

    def wheelEvent(self,event):
        factor=1.15 if event.angleDelta().y()>0 else 1/1.15
        current=abs(self.transform().m11())
        if .035<current*factor<8:self.scale(factor,factor)
        if self.follow_robot:self._center_follow_camera()
        self.viewport().update()

    def reset_view(self):
        self.follow_robot=False
        self.setSceneRect(self.arena_view_rect)
        self.fitInView(self.arena_view_rect,Qt.AspectRatioMode.KeepAspectRatio)
        self.fitted=True
        self.viewport().update()

    def set_follow(self,enabled):
        self.follow_robot=enabled
        if enabled:
            self.fitInView(QRectF(-580,-580,1160,1160),Qt.AspectRatioMode.KeepAspectRatio)
            self._center_follow_camera()
        else:self.reset_view()

    def _center_follow_camera(self):
        x,y=self.state['position'];center=QPointF(x,-y)
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
            label.setDefaultTextColor(QColor('#97b9c7'))
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
        grad=QLinearGradient(0,0,w,h);grad.setColorAt(0,QColor('#0a1621'));grad.setColorAt(1,QColor('#162c3b'))
        p.fillRect(QRectF(0,0,w,h),grad)
        self._terrain(p,w,h)
        self._geometry(p)
        self._trail(p)
        self._sources(p)
        p.restore()

    def drawForeground(self,painter,rect):
        p=painter;p.save();p.resetTransform()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=self.viewport().width(),self.viewport().height()
        self._robot(p)
        self._hud(p,w,h)
        if self.show_closeup and w>=620 and h>=420:self._closeup(p,w,h)
        p.restore()

    def _terrain(self,p,w,h):
        center=self.point((0,0));scale=abs(self.transform().m11());radius=1800*scale
        arena=QRectF(center.x()-radius,center.y()-radius,2*radius,2*radius)
        grad=QRadialGradient(center,radius)
        grad.setColorAt(0,QColor('#173242'));grad.setColorAt(.7,QColor('#122b39'));grad.setColorAt(1,QColor('#112532'))
        p.setBrush(grad);p.setPen(pen('#3c6875',1.2));p.drawEllipse(arena)
        clip=QPainterPath();clip.addEllipse(arena);p.save();p.setClipPath(clip)
        # Abstract survey contours: visual texture only, never physical obstacles.
        for i in range(10):
            a=self.point((-850+i*14,650-i*9));r=(220+i*145)*scale
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(pen(QColor(52,86,100,38),.7))
            p.drawEllipse(a,r,r*.78)
        step=250 if scale<.7 else 100
        for v in range(-2000,2001,step):
            major=v%500==0
            p.setPen(pen(QColor(60,97,112,70 if major else 30),.7))
            p.drawLine(self.point((v,-2000)),self.point((v,2000)))
            p.drawLine(self.point((-2000,v)),self.point((2000,v)))
        p.restore()
        for meters in (500,1000,1500):
            r=meters*scale;p.setPen(pen(QColor(88,128,141,45),1,Qt.PenStyle.DashLine));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center,r,r)
        for deg in range(0,360,5):
            a=math.radians(deg);outer=radius+(7 if deg%30==0 else 3)
            p.setPen(pen('#7599a6' if deg%30==0 else '#365460',1))
            p.drawLine(QPointF(center.x()+radius*math.cos(a),center.y()+radius*math.sin(a)),
                       QPointF(center.x()+outer*math.cos(a),center.y()+outer*math.sin(a)))
        # Origin launch dock, rendered as a landing pad instead of a point.
        p.save();p.translate(center)
        p.setPen(pen('#6a91a1'));p.setBrush(QColor('#152a39'));p.drawRoundedRect(QRectF(-12,-12,24,24),4,4)
        p.setPen(pen('#8bb3c1',1.4));p.drawLine(-6,0,6,0);p.drawLine(0,-6,0,6);p.restore()
        if not self.follow_robot:text(p,center.x()+17,center.y()+4,'起点 / BASE',8,'#89a8b8')

    def _trail(self,p):
        s=self.state
        path=QPainterPath(self.point((0,0)))
        for xy in s['trajectory']:path.lineTo(self.point(xy))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width,alpha in ((9,14),(5,35),(1.8,200)):
            p.setPen(pen(QColor(85,222,208,alpha),width));p.drawPath(path)
        event=s.get('active_event')
        if event and event['type']=='Move':
            target=event['data']['destination'];a=self.point(s['position']);b=self.point(target)
            p.setPen(pen(QColor(184,223,221,100),1,Qt.PenStyle.DashLine));p.drawLine(a,b)
            p.setPen(pen('#b8dfdd',1));p.drawLine(b+QPointF(-5,0),b+QPointF(5,0));p.drawLine(b+QPointF(0,-5),b+QPointF(0,5))
        if self.show_annotations:
            p.setBrush(QColor('#99d9d2'));p.setPen(Qt.PenStyle.NoPen)
            for action in s['actions']:p.drawEllipse(self.point(action['position']),2.3,2.3)

    def _sources(self,p):
        s=self.state;scale=abs(self.transform().m11())
        for source in self.sources:
            center=self.point((source['x'],source['y']));ch=source['channel'];cleared=ch in s['cleared']
            if self.radii:
                radius=source['recv_radius']*scale
                p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(pen(QColor(211,174,112,38),1,Qt.PenStyle.DashLine));p.drawEllipse(center,radius,radius)
            if self.truth:
                paint_beacon(p,center,ch,cleared,pulse=s['time']*.8,scale=.68 if not self.follow_robot else .9)
                if self.show_annotations or ch==s['channel']:
                    text(p,center.x()+13,center.y()-12,f'CH {ch:02}',9,'#68c8aa' if cleared else AMBER)
        # Clear radius and outcome are observational events, independent of truth toggle.
        for clear in s['clear_actions']:
            pos=self.point(clear['position']);radius=20*scale
            p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(pen(QColor('#70dfb2' if clear['result']=='success' else '#f27f87'),1.5));p.drawEllipse(pos,radius,radius)
        anim=s.get('animation',{});latest=anim.get('latest_clear');stamp=anim.get('last_clear_time')
        if latest and stamp is not None:
            age=s['time']-stamp
            if 0<=age<8:
                center=self.point(latest['position']);color=QColor('#74e4b1' if latest['result']=='success' else '#f48f97')
                color.setAlpha(round(160*(1-age/8)));p.setPen(pen(color,2));p.setBrush(Qt.BrushStyle.NoBrush);p.drawEllipse(center,10+age*6,10+age*6)
        if 'active_clear' in s:
            center=self.point(s['position']);radius=20*scale
            p.setPen(pen(AMBER,1.7,Qt.PenStyle.DashLine));p.setBrush(QColor(244,191,114,28));p.drawEllipse(center,radius,radius)

    def _geometry(self,p):
        s=self.state;scale=abs(self.transform().m11());channel=s['channel']
        for d in s['measurements']:
            if d['channel']!=channel:continue
            x,y=d['position'];a=math.radians(d['svd_deg'])
            apex=self.point((x,y));ends=[self.point((x+4000*math.cos(a+sign*math.pi/180),y+4000*math.sin(a+sign*math.pi/180))) for sign in (-1,1)]
            path=QPainterPath(apex);path.lineTo(ends[0]);path.lineTo(ends[1]);path.closeSubpath()
            grad=QLinearGradient(apex,(ends[0]+ends[1])*.5);grad.setColorAt(0,QColor(240,198,107,42));grad.setColorAt(1,QColor(240,198,107,0))
            p.setPen(pen(QColor(225,190,110,45),.7));p.setBrush(grad);p.drawPath(path)
            p.setPen(pen(QColor(228,196,114,130),.9,Qt.PenStyle.DashLine));p.drawLine(apex,(ends[0]+ends[1])*.5)
        loc=s['localizations'].get(channel)
        if loc:
            vertices=[self.point(v) for v in loc['vertices']]
            if len(vertices)>=3:
                p.setPen(pen(VIOLET,1.6));p.setBrush(QColor(181,152,255,70));p.drawPolygon(QPolygonF(vertices))
            elif len(vertices)==2:p.setPen(pen(VIOLET,2));p.drawLine(*vertices)
            if loc.get('farthest_pair'):
                p.setPen(pen('#dacbff',1.3));p.drawLine(*(self.point(v) for v in loc['farthest_pair']))
            if loc.get('center') is not None and loc.get('radius') is not None:
                center=self.point(loc['center']);r=loc['radius']*scale
                p.setPen(pen(QColor(181,160,245,140),1.2,Qt.PenStyle.DashLine));p.setBrush(Qt.BrushStyle.NoBrush);p.drawEllipse(center,r,r)
        for xy in s['candidates']:
            center=self.point(xy);p.setPen(pen('#9cbcf3'));p.setBrush(Qt.BrushStyle.NoBrush);p.drawRect(QRectF(center.x()-4,center.y()-4,8,8))

    def _robot(self,p):
        s=self.state;pos=self.point(s['position']);anim=s.get('animation',{})
        moving=s['status']=='moving';measuring=s['status']=='measuring'
        heading=-anim.get('heading_deg',0)
        if measuring:
            phase=anim.get('phase',0)
            for index in range(3):
                wave=(phase*2+index/3)%1;r=18+wave*62
                p.setPen(pen(QColor(102,217,221,round(100*(1-wave))),1.2));p.setBrush(Qt.BrushStyle.NoBrush);p.drawEllipse(pos,r,r)
        p.setBrush(Qt.BrushStyle.NoBrush);p.setPen(pen(QColor(98,224,209,120),1))
        p.drawEllipse(pos,35 if self.follow_robot else 26,35 if self.follow_robot else 26)
        paint_robot(p,pos,heading_deg=heading,gait_phase=anim.get('phase',0)*math.tau if s['status']=='measuring' else s['distance']*abs(self.transform().m11())*math.tau/24,
                    moving=moving,measuring=measuring,scale=.7 if self.follow_robot else .52)
        # Crosshair marks the exact pose while the artwork uses a legible illustrative size.
        p.setPen(pen('#e2fff9',1));p.drawLine(pos+QPointF(-3,0),pos+QPointF(3,0));p.drawLine(pos+QPointF(0,-3),pos+QPointF(0,3))

    def _hud(self,p,w,h):
        s=self.state;status=STATES.get(s['status'],s['status'])
        text(p,22,31,'FIELD OPERATIONS',9,'#6e99ab',True)
        text(p,22,59,'自主探索 · 实时任务视图',17,'#e0eef3',True)
        text(p,22,81,'R 1800 m    /    实线为已执行轨迹',9,'#8ca8b7')
        color=AMBER if s['status'] in ('measuring','clear','switching') else TEAL
        chip=QRectF(22,95,194,31);rounded(p,chip,'#102b37','#244c58',8)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor(color));p.drawEllipse(QPointF(35,110),3,3)
        text(p,47,115,status,10,color,True)
        # North compass + fixed-distance ruler (zoom-aware).
        cx=w-42;cy=42;p.setPen(pen('#8baebe',1.2));p.drawLine(QPointF(cx,cy+12),QPointF(cx,cy-12))
        p.drawLine(QPointF(cx,cy-12),QPointF(cx-4,cy-6));p.drawLine(QPointF(cx,cy-12),QPointF(cx+4,cy-6))
        text(p,cx-4,cy-19,'N',9,'#cedee7',True)
        scale=abs(self.transform().m11());meters=500 if scale<.3 else 100
        length=meters*scale
        p.setPen(pen('#8baebe',1));p.drawLine(QPointF(w-22-length,h-24),QPointF(w-22,h-24))
        p.drawLine(QPointF(w-22-length,h-28),QPointF(w-22-length,h-20));p.drawLine(QPointF(w-22,h-28),QPointF(w-22,h-20))
        text(p,w-22-length,h-34,f'{meters} m',9,'#a8c1cc')
        text(p,w-220,24,'跟随视角' if self.follow_robot else '全域视角',9,'#a1bac5')
        text(p,w-220,42,'图标采用示意尺寸',8,'#698696')
        if not self.truth:text(p,w-220,61,'源图标隐藏 · 接收圆仍为真值' if self.radii else 'GROUND TRUTH · HIDDEN',8,'#6bafa7')
        if s['status']=='mission ended':
            total=len(self.sources)
            text(p,w-260,82,f"任务结束    已清除 {len(s['cleared'])} / {total}",11,TEAL,True)

    def _closeup(self,p,w,h):
        s=self.state;anim=s.get('animation',{});box=QRectF(20,h-282,296,260)
        p.save();shadow=box.translated(0,5);rounded(p,shadow,'#070f17','#070f17',14)
        rounded(p,box,'#10212e','#35515f',14)
        text(p,box.x()+16,box.y()+23,'ROBOT CAMERA',9,'#78a5b5',True)
        text(p,box.x()+16,box.y()+44,'机器狗近景',13,'#e1edf2',True)
        text(p,box.right()-91,box.y()+23,f"CH {s['channel']:02d}",10,TEAL,True)
        # Closeup floor uses robot motion for a moving ground cue, never alters its pose.
        # Allow the full rotated gait envelope, including raised feet. The
        # 146 px floor leaves margin around the 1.3x robot at every gait phase.
        floor=QRectF(box.x()+10,box.y()+53,box.width()-20,146)
        p.save();p.setClipRect(floor)
        shift=(s['distance']*.1)%24
        p.setPen(pen(QColor(85,124,140,35),1))
        for i in range(-2,16):
            x=floor.x()+i*24-shift;p.drawLine(QPointF(x,floor.top()),QPointF(x,floor.bottom()))
        for i in range(8):
            y=floor.y()+i*24;p.drawLine(QPointF(floor.left(),y),QPointF(floor.right(),y))
        center=QPointF(box.center().x(),box.y()+126)
        if s['status']=='measuring':
            phase=anim.get('phase',0);r=31+phase*34
            p.setBrush(QColor(87,210,205,9));p.setPen(pen(QColor(108,226,211,80),1));p.drawEllipse(center,r,r*.58)
            a=phase*math.tau*2;end=center+QPointF(math.cos(a)*92,math.sin(a)*44)
            p.setPen(pen('#60d9d1',2));p.drawLine(center,end)
        elif s['status']=='clear':
            phase=anim.get('phase',0);p.setPen(pen(AMBER,1,Qt.PenStyle.DashLine));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center,85,49)
            for sign in (-1,1):
                x=center.x()+sign*72;p.drawLine(QPointF(x,center.y()-25),QPointF(x,center.y()+25))
            a=(phase-.5)*.9
            p.setPen(pen(QColor(253,198,108,190),2));p.drawLine(center+QPointF(45,0),center+QPointF(118,math.sin(a)*50))
        paint_robot(p,center,heading_deg=-18,gait_phase=anim.get('phase',0)*math.tau if s['status']=='measuring' else s['distance']*abs(self.transform().m11())*math.tau/24,
                    moving=s['status']=='moving',measuring=s['status']=='measuring',scale=1.3)
        p.restore()
        state_label=STATES.get(s['status'],s['status'])
        if s['status']=='moving':state_label='四足行进  ·  5.0 m/s'
        if s['status']=='measuring':state_label='天线扫描  ·  测向采集 5 s'
        if s['status']=='switching':state_label='接收机调谐  ·  切频 1 s'
        if s['status']=='clear':state_label='20 m 光学搜索与清除'
        text(p,box.x()+16,box.y()+220,state_label,10,'#d7e8ef',True)
        track=QRectF(box.x()+16,box.y()+232,box.width()-32,4)
        p.setPen(Qt.PenStyle.NoPen);p.setBrush(QColor('#233f4e'));p.drawRoundedRect(track,2,2)
        phase=anim.get('phase',0)
        if phase:p.setBrush(QColor(AMBER if s['status']!='moving' else TEAL));p.drawRoundedRect(QRectF(track.x(),track.y(),track.width()*phase,4),2,2)
        text(p,box.x()+16,box.bottom()-9,f"X {s['position'][0]:.1f}    Y {s['position'][1]:.1f} m",8,'#7c9dac')
        p.restore()
