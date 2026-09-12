"""Observer-only, automatically fitted bearing and localization detail panel."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QSize
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget, QSizePolicy

from .robot_art import paint_robot

INK = '#294858'
MUTED = '#758b98'
PURPLE = '#8261c5'
COLORS = ('#4e7cae', '#c38a40', '#3d978d', '#9975bb', '#ba727c', '#68888a')


def _pen(color, width=1, dashed=False):
    pen=QPen(QColor(color),width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    if dashed:pen.setStyle(Qt.PenStyle.DashLine)
    return pen


def _text(p,x,y,value,size=9,color=MUTED,bold=False):
    font=QFont('Arial',size);font.setBold(bold)
    p.setFont(font);p.setPen(QColor(color));p.drawText(QPointF(x,y),str(value))


class LocalizationView(QWidget):
    """Light card for a QDockWidget, using only the current replay projection.

    ``update_state(state)`` returns whether a live source task has at least one
    completed direction observation and is not cleared. ``channel`` and ``title``
    are public for the dock header. ``reset_selection()`` clears all display data.
    Selection is stateless with respect to time: scrubbing never retains a target
    from a later event or an unrelated mission.
    """
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setMinimumSize(300,490)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setAutoFillBackground(False)
        self.reset_selection()

    def sizeHint(self):
        return QSize(350,720)

    def reset_selection(self):
        self.channel=None;self.title='定位详图'
        self.state={};self.measurements=[];self.localization=None
        self.update()

    def update_state(self,state):
        context=state.get('strategy',{})
        target=context.get('current_target') or {}
        phase=context.get('phase')
        role=target.get('role','')
        active=phase=='service' or role in ('selected_service','target_followup','mec','near')
        channel=target.get('channel') if active else None
        if channel is None and phase=='service':
            channel=next((item.get('channel') for item in context.get('tasks',[])
                          if item.get('kind') in ('service','measure','clear')
                          and item.get('channel') is not None),None)
        if channel is None:channel=state.get('localization_focus_channel')
        measurements=[item for item in state.get('measurements',[])
                      if item.get('channel')==channel]
        visible=(channel is not None and channel not in state.get('cleared',set())
                 and bool(measurements) and state.get('status')!='mission ended')
        if not visible:
            self.reset_selection();return False
        self.channel=channel;self.title=f'定位详图 · CH {channel:02d}'
        self.state=state;self.measurements=measurements
        self.localization=state.get('localizations',{}).get(channel)
        self.update();return True

    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(),QColor('#f8fafb'))
        w,h=self.width(),self.height()
        if self.channel is None:
            _text(p,18,32,'进入目标定位任务后自动展开',10,INK)
            _text(p,18,55,'仅展示已经完成的测向与定位结果。',9)
            return
        _text(p,16,27,f'CH {self.channel:02d} · {len(self.measurements)} 次有效测向',13,INK,True)
        _text(p,16,48,'测点总览与交汇区域同步放大',9)
        table_rows=min(len(self.measurements),5)
        footer_height=54+table_rows*19
        chart_space=max(218,h-100-footer_height)
        overview_height=max(95,min(210,chart_space*.44))
        zoom_height=max(110,chart_space-overview_height-28)
        overview=QRectF(14,80,w-28,overview_height)
        zoom=QRectF(14,overview.bottom()+34,w-28,zoom_height)
        _text(p,16,71,'01  测点与示向',9,INK,True)
        self._plot(p,overview,False)
        _text(p,16,zoom.top()-10,'02  定位区域放大',9,INK,True)
        self._plot(p,zoom,True)
        y=zoom.bottom()+21
        diameter=(self.localization or {}).get('diameter')
        result=f'区域直径 {diameter:.2f} m' if diameter is not None else '等待第二次测向形成交汇区域'
        _text(p,16,y,result,10,PURPLE,True)
        y+=20
        _text(p,16,y,'编号',8)
        _text(p,53,y,'测点坐标 / m',8)
        _text(p,w-66,y,'示向',8)
        chosen=list(enumerate(self.measurements,1))
        if len(chosen)>5:chosen=chosen[:2]+chosen[-3:]
        for index,measurement in chosen:
            y+=19;color=COLORS[(index-1)%len(COLORS)]
            x0,y0=measurement['position']
            _text(p,16,y,f'D{index}',8,color,True)
            _text(p,53,y,f'({x0:.1f}, {y0:.1f})',8,INK)
            _text(p,w-66,y,f"{measurement['svd_deg']:.2f}°",8,color)
        p.end()

    def _bounds(self,zoom):
        loc=self.localization or {}
        vertices=[list(v) for v in loc.get('vertices',[]) if len(v)==2]
        if zoom and vertices:
            xs=[v[0] for v in vertices];ys=[v[1] for v in vertices]
            center=loc.get('center')
            radius=loc.get('radius')
            if center is not None and radius is not None:
                xs.extend([center[0]-radius,center[0]+radius])
                ys.extend([center[1]-radius,center[1]+radius])
            cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
            span=max(max(xs)-min(xs),max(ys)-min(ys),12)*1.55
            return cx-span/2,cy-span/2,cx+span/2,cy+span/2
        points=[list(m['position']) for m in self.measurements]+vertices
        if self.state.get('position') is not None:points.append(list(self.state['position']))
        target=self.state.get('strategy',{}).get('current_target') or {}
        if target.get('position') is not None:points.append(list(target['position']))
        if not vertices:
            for m in self.measurements:
                a=math.radians(m['svd_deg']);x,y=m['position']
                points.append([x+900*math.cos(a),y+900*math.sin(a)])
        xs=[v[0] for v in points];ys=[v[1] for v in points]
        cx=(min(xs)+max(xs))/2;cy=(min(ys)+max(ys))/2
        span=max(max(xs)-min(xs),max(ys)-min(ys),100)*1.22
        return cx-span/2,cy-span/2,cx+span/2,cy+span/2

    def _plot(self,p,rect,zoom):
        p.setPen(_pen('#dce6eb'));p.setBrush(QColor('#ffffff'))
        p.drawRoundedRect(rect,7,7)
        loc=self.localization or {}
        if zoom and not loc.get('vertices'):
            _text(p,rect.x()+16,rect.center().y()-3,'交汇区域尚未形成',10,INK,True)
            _text(p,rect.x()+16,rect.center().y()+18,'测向完成后自动更新，结果不会提前显示。',8)
            return
        minimum_x,minimum_y,maximum_x,maximum_y=self._bounds(zoom)
        plot=rect.adjusted(18,12,-15,-22)
        span_x,span_y=maximum_x-minimum_x,maximum_y-minimum_y
        scale=min(plot.width()/span_x,plot.height()/span_y)
        center_x=(minimum_x+maximum_x)/2;center_y=(minimum_y+maximum_y)/2
        def point(xy):
            return QPointF(plot.center().x()+(xy[0]-center_x)*scale,
                           plot.center().y()-(xy[1]-center_y)*scale)
        p.save();p.setClipRect(rect.adjusted(1,1,-1,-1))
        for n in range(1,5):
            x=plot.left()+plot.width()*n/5;y=plot.top()+plot.height()*n/5
            p.setPen(_pen('#edf2f5',.7));p.drawLine(QPointF(x,plot.top()),QPointF(x,plot.bottom()))
            p.drawLine(QPointF(plot.left(),y),QPointF(plot.right(),y))
        for index,m in enumerate(self.measurements,1):
            origin=m['position'];apex=point(origin);angle=math.radians(m['svd_deg'])
            length=math.hypot(origin[0]-center_x,origin[1]-center_y)+max(span_x,span_y)*3
            endpoints=[point((origin[0]+length*math.cos(angle+offset),
                              origin[1]+length*math.sin(angle+offset)))
                       for offset in (-math.pi/180,math.pi/180)]
            color=QColor(COLORS[(index-1)%len(COLORS)])
            fill=QColor(color);fill.setAlpha(20)
            edge=QColor(color);edge.setAlpha(130)
            p.setPen(_pen(edge,.9));p.setBrush(fill)
            p.drawPolygon(QPolygonF([apex,*endpoints]))
            centerline=point((origin[0]+length*math.cos(angle),origin[1]+length*math.sin(angle)))
            p.setPen(_pen(color,.9,True));p.drawLine(apex,centerline)
        vertices=[point(v) for v in loc.get('vertices',[])]
        if len(vertices)>=3:
            p.setPen(_pen(PURPLE,1.6));p.setBrush(QColor(144,107,199,58));p.drawPolygon(QPolygonF(vertices))
        if loc.get('farthest_pair'):
            p.setPen(_pen('#7150ad',1.25));p.drawLine(*(point(v) for v in loc['farthest_pair']))
        if loc.get('center') is not None and loc.get('radius') is not None:
            center=point(loc['center']);radius=loc['radius']*scale
            p.setPen(_pen('#9174bf',1.1,True));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center,radius,radius)
        # The clear action's 20m circle is an actual rule overlay, not a truth
        # source marker. It appears only when the projected action is active.
        if self.state.get('active_clear',{}).get('channel')==self.channel:
            center=point(self.state['position'])
            p.setPen(_pen('#b98136',1.1,True));p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(center,20*scale,20*scale)
        grouped={}
        for index,m in enumerate(self.measurements,1):
            grouped.setdefault(tuple(m['position']),[]).append(index)
        for xy,indices in grouped.items():
            anchor=point(xy)
            if not rect.adjusted(7,7,-7,-7).contains(anchor):continue
            color=COLORS[(indices[0]-1)%len(COLORS)]
            p.setPen(_pen('#ffffff',1.3));p.setBrush(QColor(color));p.drawEllipse(anchor,4.6,4.6)
            label='/'.join(f'D{i}' for i in indices)
            x=min(max(anchor.x()+8,rect.left()+5),rect.right()-12-len(label)*5)
            y=max(rect.top()+13,anchor.y()-8)
            _text(p,x,y,label,8,color,True)
        robot=point(self.state['position'])
        if rect.adjusted(14,14,-14,-14).contains(robot):
            paint_robot(p,robot,heading_deg=-self.state.get('heading_deg',0),scale=.24)
        p.restore()
        units=max(span_x,span_y)
        ruler=10**math.floor(math.log10(max(units/5,1e-6)))
        if ruler*5<units/3:ruler*=5
        elif ruler*2<units/3:ruler*=2
        length=ruler*scale;x=rect.right()-16-length;y=rect.bottom()-11
        p.setPen(_pen('#8399a7',.9));p.drawLine(QPointF(x,y),QPointF(x+length,y))
        p.drawLine(QPointF(x,y-3),QPointF(x,y+2));p.drawLine(QPointF(x+length,y-3),QPointF(x+length,y+2))
        _text(p,x,y-5,f'{ruler:g} m',7)
        if zoom:_text(p,rect.x()+10,rect.bottom()-9,'实线边界 ±1°  /  虚线示向中心',7)
