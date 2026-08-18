import unittest
from unittest import mock

from kilix_sdk import wayland_input


class WaylandInputTests(unittest.TestCase):
    @mock.patch("kilix_sdk.wayland_input.socket.socket")
    def test_keyboard_mouse_and_wheel_protocol(self, socket_class):
        sock = socket_class.return_value
        injector = wayland_input.Injector(
            "/tmp/private.sock", 800, 600, offset_x=1280)
        injector.frame_rate(20)
        self.assertTrue(injector.key("ArrowLeft", 1))
        injector.mouse({"x": 50, "y": 25, "b": 0, "press": True},
                       (0, 0, 100, 50))
        injector.mouse({"x": 50, "y": 25, "b": 64, "press": True},
                       (0, 0, 100, 50))
        self.assertEqual(
            [call.args[0] for call in sock.sendall.call_args_list],
            [b"o 1280 0\n", b"r 20 0\n", b"k 105 1\n",
             b"m 400 300\n", b"b 272 1\n",
             b"m 400 300\n", b"a 0 -1\n"])

    def test_key_mapping_is_case_insensitive_and_rejects_unknown(self):
        self.assertEqual(wayland_input.Injector.code_for("A"), 30)
        self.assertEqual(wayland_input.Injector.code_for(chr(57442)), 29)
        self.assertEqual(wayland_input.Injector.code_for("MediaPlay"), 0)

    @mock.patch("kilix_sdk.wayland_input.socket.socket")
    def test_frame_rate_preserves_60_hz_and_caps_above_it(self, socket_class):
        sock = socket_class.return_value
        injector = wayland_input.Injector("/tmp/private.sock", 800, 600)
        injector.frame_rate(60)
        injector.frame_rate(144)
        self.assertEqual(
            [call.args[0] for call in sock.sendall.call_args_list],
            [b"o 0 0\n", b"r 60 0\n", b"r 60 0\n"])


if __name__ == "__main__":
    unittest.main()
