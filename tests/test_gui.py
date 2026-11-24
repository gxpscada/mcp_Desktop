import unittest
from types import SimpleNamespace
from app import App
from core import configuration_frames

class GuiTests(unittest.TestCase):
    def setUp(self):
        self.app=App(); self.app.withdraw()
    def tearDown(self):
        self.app.destroy()
    def test_plot_missing_and_bounded(self):
        p=self.app.plot
        p.add(10, {'T0_C':20, 'T1_C':None})
        p.add(11, {'T0_C':None, 'T1_C':22})
        p.add(12, {'T0_C_float':20.125, 'T0_C':20.12})
        self.assertEqual(p.points[-1][1][0],20.125)
        self.assertIsNone(p.points[1][1][0])
        for i in range(2200): p.add(13+i,{'T0_C':20+i%3})
        self.assertEqual(len(p.points),2000)
        p.redraw()
        self.assertTrue(p.canvas.find_all())
    def test_configuration_waits_for_stream_stop(self):
        a=self.app
        a.port=SimpleNamespace(is_open=True)
        a.streaming=True; a.busy=True
        s=a.session_value(); frames=configuration_frames(s,a.settings['address'])
        sent=[]
        a.exchange=lambda t,request: sent.append(request)
        a.configure_device()
        self.assertTrue(a.stream_stop.is_set())
        self.assertFalse(a.streaming)
        self.assertEqual(sent,[])
        self.assertTrue(a.busy)
        expected=frames[1][7:-2]
        class Transport:
            log=SimpleNamespace(write=lambda *args,**kwargs:None)
            def transact(self,request):
                if request[1]==1: return {'bits_hex':'07'}
                if int.from_bytes(request[2:4],'big')==29: return {'001D':s['channel_mask']}
                return {str(i):int.from_bytes(expected[i:i+2],'big') for i in range(0,len(expected),2)}
        a.after_stream_job(Transport())
        self.assertEqual(sent,frames)
        self.assertEqual(sent[0][1:6],bytes.fromhex('0500000000'))
