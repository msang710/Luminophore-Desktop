from __future__ import annotations
from threading import Thread
import time
import gi
gi.require_version('Gtk','4.0')
from gi.repository import Gtk, GLib
from ..monitor_layout import MonitorLayout, positions, snapped


class MonitorSettings(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        self.service=MonitorLayout()
        self.data=None; self.candidate={}; self.drag=None; self.transform=(1,0,0)
        self.disposed=False
        self.active=False; self.busy=False; self.started=0
        self.append(Gtk.Label(label='모니터 배치',xalign=0))
        self.append(Gtk.Label(label='화면을 드래그해 가장자리를 맞추세요. 해상도와 배율은 유지됩니다.',xalign=0,wrap=True))
        self.canvas=Gtk.DrawingArea(content_width=550,content_height=260)
        self.canvas.set_draw_func(self.draw)
        gesture=Gtk.GestureDrag.new()
        gesture.connect('drag-begin',self.begin_drag)
        gesture.connect('drag-update',self.move_drag)
        gesture.connect('drag-end',self.end_drag)
        gesture.connect('cancel',self.cancel_drag)
        self.canvas.add_controller(gesture)
        self.append(self.canvas)
        row=Gtk.Box(spacing=8)
        self.buttons=[]
        for label,callback in [('새로고침',self.refresh),('모니터 식별',self.identify),('적용',self.apply),('이 배치 유지',self.keep),('되돌리기',self.cancel)]:
            b=Gtk.Button(label=label); b.connect('clicked',callback); row.append(b); self.buttons.append(b)
        self.append(row)
        self.status=Gtk.Label(xalign=0,wrap=True); self.append(self.status)
        self.poll_id=GLib.timeout_add(500,self.poll)
        self.connect('unrealize',self.dispose)
        self.refresh()

    def controls(self):
        if self.disposed:return
        for i,b in enumerate(self.buttons):
            b.set_sensitive(not self.busy and (self.active if i>=3 else not self.active))
        self.canvas.set_sensitive(not self.busy and not self.active)

    def worker(self,operation,complete):
        if self.busy: return
        self.busy=True; self.controls()
        def run():
            try: result,error=operation(),None
            except Exception as e: result,error=None,str(e)
            def finish():
                self.busy=False
                if self.disposed:
                    self.service.release()
                    return False
                if error: self.status.set_text(error)
                else: complete(result)
                self.controls()
                return False
            GLib.idle_add(finish)
        Thread(target=run,daemon=True).start()

    def refresh(self,*_):
        def get():
            self.service.recover()
            return self.service.snapshot()
        def done(data):
            self.data=data; self.candidate=positions(data)
            self.status.set_text('좌우·상하 배치를 편집할 수 있습니다' if data['supported'] else '현재 미러링·혼합 배율 조건은 지원하지 않습니다')
            self.canvas.queue_draw()
        self.worker(get,done)

    def draw(self,area,cr,width,height):
        if not self.data: return
        outputs=self.data['outputs']
        if not outputs: return
        if not self.drag:
            minx=min(self.candidate[o['key']][0] for o in outputs)
            miny=min(self.candidate[o['key']][1] for o in outputs)
            maxx=max(self.candidate[o['key']][0]+o['width'] for o in outputs)
            maxy=max(self.candidate[o['key']][1]+o['height'] for o in outputs)
            scale=min((width-50)/max(maxx-minx,1),(height-50)/max(maxy-miny,1))
            self.transform=(scale,25-minx*scale,25-miny*scale)
        scale,ox,oy=self.transform
        for i,o in enumerate(outputs):
            x,y=self.candidate[o['key']]
            x,y,w,h=x*scale+ox,y*scale+oy,o['width']*scale,o['height']*scale
            cr.set_source_rgba(.2,.65,.7,.15); cr.rectangle(x,y,w,h); cr.fill_preserve()
            cr.set_source_rgba(.4,.85,.9,.9); cr.set_line_width(2); cr.stroke()
            cr.set_source_rgba(.9,.95,1,1); cr.set_font_size(14); cr.move_to(x+10,y+24); cr.show_text(f"{i+1} · {o['name']}")

    def begin_drag(self,gesture,x,y):
        if self.active or self.busy or not self.data:return
        scale,ox,oy=self.transform
        x,y=(x-ox)/scale,(y-oy)/scale
        for o in self.data['outputs']:
            a,b=self.candidate[o['key']]
            if a<=x<=a+o['width'] and b<=y<=b+o['height']:
                self.drag=(o['key'],a,b,scale);break

    def move_drag(self,gesture,dx,dy):
        if not self.drag:return
        key,x,y,scale=self.drag
        self.candidate[key]=list(snapped(self.data,self.candidate,key,x+dx/scale,y+dy/scale,distance=12/scale))
        self.canvas.queue_draw()

    def end_drag(self,gesture,dx,dy):
        self.move_drag(gesture,dx,dy);self.drag=None;self.canvas.queue_draw()

    def cancel_drag(self,*_):
        if self.drag:
            key,x,y,_=self.drag;self.candidate[key]=[x,y]
        self.drag=None;self.canvas.queue_draw()

    def apply(self,*_):
        if not self.data:return
        data=self.data;candidate={k:list(v) for k,v in self.candidate.items()}
        def done(_):
            self.active=True;self.started=time.monotonic()
            self.status.set_text('15초 안에 이 배치 유지를 선택하세요. 확인하지 않으면 복구됩니다.')
        self.worker(lambda:self.service.begin(data,candidate),done)

    def keep(self,*_):
        def done(_):
            self.active=False;self.status.set_text('배치 저장 완료')
        self.worker(self.service.confirm,done)

    def cancel(self,*_):
        def done(_):
            self.active=False;self.status.set_text('이전 배치 복구 완료')
        self.worker(self.service.cancel,done)

    def poll(self):
        if self.active and not self.busy:
            def done(state):
                if state not in ('preview','confirming'):
                    self.active=False
                    # Cleanup retains a recoverable pending file on any error.
                    self.status.set_text('적용 종료: '+state+' · 새로고침으로 현재 배치를 확인하세요')
                    self.service.release()
                else:self.status.set_text(f"배치를 유지할까요? 약 {max(0,15-int(time.monotonic()-self.started))}초 후 자동 복구")
            self.worker(self.service.status,done)
        return True

    def identify(self,*_):
        gi.require_version('Gdk','4.0');gi.require_version('Gtk4LayerShell','1.0')
        from gi.repository import Gdk,Gtk4LayerShell
        display=Gdk.Display.get_default()
        if not display or not self.data:return
        numbers={o['name']:i+1 for i,o in enumerate(self.data['outputs'])}
        monitors=display.get_monitors()
        for i in range(monitors.get_n_items()):
            monitor=monitors.get_item(i);name=monitor.get_connector()
            if name not in numbers:continue
            window=Gtk.Window(decorated=False)
            Gtk4LayerShell.init_for_window(window)
            Gtk4LayerShell.set_namespace(window,'luminophore-monitor-identify')
            Gtk4LayerShell.set_monitor(window,monitor)
            Gtk4LayerShell.set_layer(window,Gtk4LayerShell.Layer.OVERLAY)
            label=Gtk.Label(label=f"{numbers[name]} · {name}")
            for edge in ('top','bottom','start','end'):getattr(label,'set_margin_'+edge)(24)
            window.set_child(label);window.present()
            GLib.timeout_add(1800,lambda w=window:(w.destroy(),False)[1])

    def dispose(self,*_):
        self.disposed=True
        if self.poll_id:GLib.source_remove(self.poll_id);self.poll_id=0
        # No blocking IPC in widget destruction; compositor lease expires even
        # if both the settings app and Shell die. Keep durable pending fallback.
        if not self.busy:self.service.release()
