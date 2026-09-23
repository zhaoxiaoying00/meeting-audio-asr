import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'_app'))
from device_routing import choose_endpoint

class RoutingTests(unittest.TestCase):
    def test_virtual_default_after_unplug_prefers_physical_microphone(self):
        virtual='麦克风阵列 (网易虚拟音频设备)';real='麦克风阵列 (Intel Smart Sound)'
        devices=[{'name':virtual},{'name':real}]
        self.assertEqual(choose_endpoint(devices,virtual,virtual)[0],real)

    def test_inactive_display_default_prefers_speakers(self):
        monitor='DELL P2419H (HD Audio Driver for Display Audio)';real='扬声器 (Realtek(R) Audio)'
        self.assertEqual(choose_endpoint([{'name':monitor},{'name':real}],monitor,monitor,True)[0],real)

    def test_actual_conference_device_overrides_fallback(self):
        virtual='Microphone (Virtual Audio)'
        self.assertEqual(choose_endpoint([{'name':virtual,'conference':True},{'name':'Microphone (USB)'}],'Microphone (USB)')[0],virtual)

    def test_actual_playback_on_monitor_is_respected(self):
        monitor='Monitor (Display Audio)'
        self.assertEqual(choose_endpoint([{'name':monitor,'active':True},{'name':'Speakers'}],'Speakers','Speakers',True)[0],monitor)

    def test_connected_headset_default_stays_selected(self):
        self.assertEqual(choose_endpoint([{'name':'Microphone Array'},{'name':'Headset Microphone'}],'Headset Microphone')[0],'Headset Microphone')

    def test_virtual_only_fallback_and_no_devices(self):
        self.assertEqual(choose_endpoint([{'name':'Virtual Audio'}],'Virtual Audio')[0],'Virtual Audio')
        self.assertIsNone(choose_endpoint([]))

if __name__=='__main__':unittest.main()
