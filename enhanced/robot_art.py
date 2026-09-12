"""Resolution independent robot and transmitter artwork for observer views.

All coordinates are screen coordinates: a heading of zero faces right and positive
headings turn clockwise. ``gait_phase`` is in radians. Artwork is entirely cosmetic
and never changes the true pose supplied by the simulation.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen,
    QPolygonF, QRadialGradient,
)


def _pen(color: str | QColor, width: float = 1.0) -> QPen:
    result = QPen(QColor(color), width)
    result.setCapStyle(Qt.PenCapStyle.RoundCap)
    result.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return result


def _polygon(painter: QPainter, points, fill, stroke=None, width=1.0) -> None:
    painter.setPen(_pen(stroke, width) if stroke else Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))


def _linear(x1, y1, x2, y2, stops):
    gradient = QLinearGradient(x1, y1, x2, y2)
    for position, color in stops:
        gradient.setColorAt(position, QColor(color))
    return gradient


def _ellipse(painter, rect, fill, stroke=None, width=1):
    painter.setPen(_pen(stroke, width) if stroke else Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawEllipse(QRectF(*rect))


def _rounded(painter, rect, radius, fill, stroke=None, width=1):
    painter.setPen(_pen(stroke, width) if stroke else Qt.PenStyle.NoPen)
    painter.setBrush(fill)
    painter.drawRoundedRect(QRectF(*rect), radius, radius)


def paint_robot(
    painter: QPainter,
    center: QPointF,
    heading_deg: float = 0,
    gait_phase: float = 0,
    moving: bool = False,
    measuring: bool = False,
    scale: float = 1.0,
    accent: QColor = QColor("#66e0d5"),
) -> None:
    """Paint a detailed quadruped, approximately 110 × 78 logical pixels.

    ``center`` is the exact pose; leg articulation does not translate the robot.
    Diagonal limbs share a phase. Idle limbs remain planted, and the lidar rotates
    while measuring. The caller's painter state is always restored.
    """
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(center)
        painter.rotate(heading_deg)
        painter.scale(scale, scale)
        accent = QColor(accent)

        # A soft contact shadow separates the machine from its map without a
        # bitmap or platform-dependent effect. Its falloff stays transparent.
        shadow = QRadialGradient(QPointF(-1, 6), 55)
        shadow.setColorAt(0, QColor(1, 6, 12, 150))
        shadow.setColorAt(.57, QColor(1, 6, 12, 90))
        shadow.setColorAt(1, QColor(1, 6, 12, 0))
        _ellipse(painter, (-56, -32, 112, 76), shadow)

        # Draw the four articulated legs behind the body. Each has an actuator,
        # aluminium upper linkage, dark lower linkage, rubber foot and fasteners.
        for fore, root_x in ((False, -24.0), (True, 22.0)):
            for side in (-1, 1):
                diagonal = (fore and side < 0) or (not fore and side > 0)
                phase = gait_phase + (0 if diagonal else math.pi)
                stride = 6.8 * math.sin(phase) if moving else 0
                lift = max(0.0, math.cos(phase)) if moving else 0
                hip = QPointF(root_x, side * 16)
                knee = QPointF(root_x - 7 + stride * .50, side * (25 - lift * 1.5))
                ankle = QPointF(root_x + 1 + stride, side * (34 - lift * 3.5))

                _ellipse(painter, (ankle.x() - 6, ankle.y() - 2 + 3,
                                  14, 6), QColor(0, 6, 12, 75))
                painter.setPen(_pen("#101c29", 9))
                painter.drawLine(hip, knee)
                painter.setPen(_pen("#526b80", 6.3))
                painter.drawLine(hip, knee)
                painter.setPen(_pen("#c7d8e3", 2.5))
                painter.drawLine(QPointF(hip.x(), hip.y() - 1),
                                 QPointF(knee.x(), knee.y() - 1))

                painter.setPen(_pen("#0a1520", 6.5))
                painter.drawLine(knee, ankle)
                painter.setPen(_pen("#6d8898", 3.4))
                painter.drawLine(knee, ankle)
                painter.setPen(_pen("#dbe9ef", .8))
                painter.drawLine(QPointF(knee.x(), knee.y() - 1),
                                 QPointF(ankle.x(), ankle.y() - 1))

                _ellipse(painter, (knee.x()-4, knee.y()-4, 8, 8),
                         "#8298a8", "#152532", .9)
                _ellipse(painter, (knee.x()-1.8, knee.y()-1.8, 3.6, 3.6),
                         "#233b4c", "#b5c9d4", .55)
                _rounded(painter, (ankle.x()-5.4, ankle.y()-3.4, 12, 6.8), 2.4,
                         _linear(0, ankle.y()-3, 0, ankle.y()+3,
                                 [(0, "#405566"), (.5, "#192a37"), (1, "#07111b")]),
                         "#587082", .8)
                painter.setPen(_pen("#74909f", .7))
                painter.drawLine(QPointF(ankle.x()+3, ankle.y()-1.7),
                                 QPointF(ankle.x()+3, ankle.y()+1.7))
                _ellipse(painter, (root_x-5.6, side*16-5.6, 11.2, 11.2),
                         "#2d4659", "#0b1723", 1)
                _ellipse(painter, (root_x-3.4, side*16-3.4, 6.8, 6.8),
                         "#a5bbc9", "#e3eff4", .5)

        # Belly and seam shadow establish the layered height of the shell.
        _rounded(painter, (-35, -16, 70, 34), 10, "#081723", "#678397", 1)
        _rounded(painter, (-32, -17, 64, 31), 9,
                 _linear(0, -17, 0, 14, [(0, "#7d9baa"), (.5, "#39596e"),
                                       (1, "#18394d")]), "#9db5c4", .8)

        # Front sensor neck and head with a broad black camera visor.
        _rounded(painter, (29, -10, 13, 20), 4, "#172d3c", "#607b8e", .9)
        for y in (-5, 0, 5):
            painter.setPen(_pen("#8198a6", .85))
            painter.drawLine(QPointF(33, y), QPointF(37, y))
        head = [(38,-12), (48,-10), (54,-5), (54,5), (48,10), (38,12), (35,7), (35,-7)]
        _polygon(painter, head,
                 _linear(35, -12, 50, 12, [(0,"#e6f1f4"), (.43,"#8faabc"),
                                         (1,"#345c74")]), "#aec7d4", .9)
        _polygon(painter, [(47,-8),(52,-4),(52,4),(47,8),(43,7),(43,-7)],
                 _linear(43,-8,52,8,[(0,"#152b3b"),(.5,"#071621"),(1,"#285169")]),
                 "#799dad", .6)
        for y in (-4,4):
            _ellipse(painter, (46.3, y-1.9, 3.8, 3.8), "#153243", "#81a6b6", .5)
            _ellipse(painter, (47.3, y-.85, 1.7, 1.7), accent)
            _ellipse(painter, (47.9, y-.65, .6, .6), "#f2ffff")
        painter.setPen(_pen("#ffb867",1.9))
        painter.drawLine(QPointF(40,-7), QPointF(40,-3))
        painter.drawLine(QPointF(40,3), QPointF(40,7))

        # Main shell: a long pale metallic top, blue faceted lower edges, and
        # a restrained seam around a dark service panel.
        shell = [(-33,-9),(-26,-16),(23,-16),(32,-10),(33,8),(25,14),
                 (-26,14),(-34,7)]
        _polygon(painter, shell,
                 _linear(-15,-17,8,17,[(0,"#f0f6f7"),(.26,"#d5e5eb"),
                                      (.62,"#b4cbd8"),(1,"#6c94ab")]),
                 "#bed4df", .85)
        _polygon(painter, [(-33,-9),(-26,-16),(23,-16),(30,-11),(-25,-11),(-32,-5)],
                 QColor("#f5fafb"))
        _polygon(painter, [(-34,7),(-26,14),(25,14),(32,8),(25,9),(-25,9)],
                 "#517d97")
        _rounded(painter, (-22,-10,42,19), 4.5,
                 _linear(0,-10,0,9,[(0,"#3c5d72"),(.5,"#203c50"),(1,"#112b3e")]),
                 "#88a9bb", .85)
        painter.setPen(_pen(QColor(225,248,255,120), .55))
        painter.drawLine(QPointF(-18,-8), QPointF(14,-8))

        # Battery radiator louvers and an amber latch at the back.
        for x in (-28,-25,-22):
            painter.setPen(_pen("#476579", 1.3))
            painter.drawLine(QPointF(x,-6), QPointF(x,5))
        _rounded(painter, (-32,-4,3,8), 1, "#eaa461", "#fff0c3", .5)
        for x,y in ((-24,-12),(23,-11),(-25,9),(25,8)):
            _ellipse(painter,(x-1.15,y-1.15,2.3,2.3),"#496a7d","#eaf5f8",.4)
            painter.setPen(_pen("#c8d9df",.4))
            painter.drawLine(QPointF(x-.5,y),QPointF(x+.5,y))

        # A low lidar turret: base ring, shaded cap, glass aperture and rotating
        # optical reflection. Rotation is bounded to observer artwork.
        _ellipse(painter,(-8,-8,20,20),QColor(1,12,23,95))
        _ellipse(painter,(-9,-10,20,20),"#0b2436","#83aabd",1)
        _ellipse(painter,(-7.7,-8.7,17.4,17.4),
                 _linear(-7,-9,8,8,[(0,"#bed5df"),(.27,"#83a9ba"),
                                    (.54,"#305772"),(1,"#162e43")]),"#d2e8ef",.55)
        _ellipse(painter,(-5.7,-6.7,13.4,13.4),"#102b3e","#a1c4d2",.6)
        _ellipse(painter,(-4.4,-5.4,10.8,10.8),
                 _linear(-4,-5,5,5,[(0,"#40657d"),(.4,"#254860"),(1,"#0e2c40")]))
        painter.save()
        painter.translate(1,0)
        painter.rotate(math.degrees(gait_phase*1.5) if measuring else -35)
        glow=QColor(accent); glow.setAlpha(180 if measuring else 95)
        painter.setPen(_pen(glow,1.35))
        painter.drawArc(QRectF(-5,-5,10,10), 18*16, 86*16)
        painter.setPen(_pen("#ddfcff",.65))
        painter.drawLine(QPointF(0,0),QPointF(3.3,-2.4))
        painter.restore()
        _ellipse(painter,(-.1,-1.1,2.2,2.2),"#bfd6df")

        # Forward status stripe and rear equipment antenna, all attached to body.
        _rounded(painter,(20,-6,3,12),1.3,accent,"#b9fcfa",.35)
        _rounded(painter,(25,-4,2,8),.9,"#64879a")
        painter.setPen(_pen("#102939",2.2))
        painter.drawLine(QPointF(-18,-7),QPointF(-23,-21))
        painter.setPen(_pen("#a6c3d1",.8))
        painter.drawLine(QPointF(-18.6,-7.5),QPointF(-23.5,-21))
        _ellipse(painter,(-25,-23,4,4),"#172e40","#8cacbd",.6)
        _ellipse(painter,(-24,-22,2,2),accent)
        # Two micro rivets and tiny deck bars read as real manufactured detail at
        # high zoom and merge cleanly at the mission map's smaller scale.
        for y in (-4,0,4):
            painter.setPen(_pen("#85a3b4",.75))
            painter.drawLine(QPointF(-16,y),QPointF(-12,y))
    finally:
        painter.restore()


def paint_beacon(
    painter: QPainter,
    center: QPointF,
    channel: int,
    cleared: bool,
    pulse: float = 0,
    scale: float = 1.0,
) -> None:
    """Paint a transmitter whose ground anchor is exactly at ``center``.

    The compact icon spans about 30 × 40 pixels above its ground anchor. ``pulse``
    is a phase in radians. Cleared transmitters retain their location and channel
    badge, with a green status check and no transmitting waves.
    """
    painter.save()
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.translate(center)
        painter.scale(scale,scale)
        hot=QColor("#f2b96d") if not cleared else QColor("#77b49f")
        base=QColor("#a9824a") if not cleared else QColor("#587678")
        _ellipse(painter,(-17,-2,34,13),QColor(0,7,15,100))
        if not cleared:
            alpha=round(27+14*(1+math.sin(pulse)))
            aura=QColor(hot); aura.setAlpha(alpha)
            _ellipse(painter,(-17,-7,34,20),aura)
        # Perspective mounting plinth and a ribbed transmitter equipment cabinet.
        _polygon(painter,[(-12,0),(-3,-5),(12,0),(3,6)],"#182b38","#6e8791",.6)
        _polygon(painter,[(-12,0),(3,6),(3,9),(-12,3)],"#1b303d","#5a727e",.5)
        _polygon(painter,[(3,6),(12,0),(12,3),(3,9)],"#0c202c","#425c69",.5)
        _rounded(painter,(-8,-16,16,21),2.8,
                 _linear(-8,-15,8,5,[(0,"#7f969c" if cleared else "#b9a37d"),
                                     (.42,"#445e69"),(1,"#1f3948")]),
                 "#a2b5bb",.65)
        _polygon(painter,[(-8,-13),(-5,-17),(6,-17),(8,-14)],
                 "#7e9b9d" if cleared else "#d1b587","#c4d1ca",.5)
        _rounded(painter,(-5,-11,10,8),1.4,"#112c3d","#728d97",.5)
        for y in (-8.7,-6.7,-4.7):
            painter.setPen(_pen("#507382",.65))
            painter.drawLine(QPointF(-3,y),QPointF(3,y))
        _rounded(painter,(-5,-.5,7,2.2),.7,hot)
        _ellipse(painter,(3,-.2,1.5,1.5),"#daf5cf" if cleared else "#ffe1a0")
        # Mast with alternating metallic highlights and a warm antenna tip.
        painter.setPen(_pen("#091d2b",3.4))
        painter.drawLine(QPointF(0,-16),QPointF(0,-30))
        painter.setPen(_pen("#bfd1d4",1.5))
        painter.drawLine(QPointF(-.4,-17),QPointF(-.4,-30))
        _rounded(painter,(-2.7,-26,5.4,3.3),1,base,"#e2cfb0" if not cleared else "#a6b9ba",.5)
        _ellipse(painter,(-2,-32,4,4),hot,"#ecdec4" if not cleared else "#bad2cf",.6)
        if not cleared:
            wave=QColor(hot); wave.setAlpha(round(110+60*(1+math.sin(pulse))/2))
            painter.setPen(_pen(wave,1.1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for radius in (6,10):
                rect=QRectF(-radius,-30-radius,radius*2,radius*2)
                painter.drawArc(rect, -42*16, 84*16)
                painter.drawArc(rect, 138*16, 84*16)
        else:
            _ellipse(painter,(5,-23,12,12),"#214f4d","#76bdac",.7)
            painter.setPen(_pen("#baf7df",1.6))
            painter.drawLine(QPointF(8,-17),QPointF(10,-15))
            painter.drawLine(QPointF(10,-15),QPointF(14,-20))
        # The badge belongs to the equipment, leaving map annotation placement to
        # its caller. It remains readable at small sizes without a text bubble.
        painter.setFont(QFont("Arial",5,QFont.Weight.Bold))
        painter.setPen(QColor("#e3eef1"))
        painter.drawText(QRectF(-8,1,16,7),Qt.AlignmentFlag.AlignCenter,str(channel))
    finally:
        painter.restore()
