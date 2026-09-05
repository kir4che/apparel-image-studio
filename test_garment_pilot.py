import copy
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

import garment_pilot as pilot


GOOD = {"product_present": True, "variant": "beige zebra", "image_type": "full_body",
        "view": "front", "detail": "whole dress", "garment_box": [200, 160, 800, 850],
        "full_head_visible": True, "face_cut_y": 100, "reason": "Keep lower face", "concerns": []}


def answer(data=GOOD):
    return {"output": [{"type": "message", "content": json.dumps(data)}], "stats": {"total_output_tokens": 100}}


class PilotTests(unittest.TestCase):
    def tearDown(self):
        pilot._llm = None
        pilot._engine = None

    def test_windows_model_requires_matching_vision_projector(self):
        with tempfile.TemporaryDirectory() as d:
            model_dir = Path(d)
            model = model_dir / pilot.PREFERRED_MODEL_FILE
            model.write_bytes(b"model")
            with patch.object(pilot, "MODEL_DIR", model_dir), patch.object(pilot, "get_engine", return_value="llamacpp"):
                engine, detail = pilot.detect_engine()
                self.assertIsNone(engine)
                self.assertIn(pilot.PREFERRED_PROJECTOR_FILE, detail)
                projector = model_dir / pilot.PREFERRED_PROJECTOR_FILE
                projector.write_bytes(b"projector")
                engine, detail = pilot.detect_engine()
                self.assertEqual(engine, "llamacpp")
                self.assertIn(model.name, detail)
                self.assertIn(projector.name, detail)

    def test_builtin_llm_loads_vision_projector(self):
        with tempfile.TemporaryDirectory() as d:
            model_dir = Path(d)
            model = model_dir / pilot.PREFERRED_MODEL_FILE
            projector = model_dir / pilot.PREFERRED_PROJECTOR_FILE
            model.write_bytes(b"model")
            projector.write_bytes(b"projector")
            fake_llama = MagicMock()
            fake_handler = MagicMock()
            with patch.object(pilot, "MODEL_DIR", model_dir), \
                    patch("llama_cpp.Llama", return_value=fake_llama) as llama, \
                    patch("llama_cpp.llama_chat_format.MTMDChatHandler", return_value=fake_handler) as handler:
                self.assertIs(pilot._get_llm(), fake_llama)
            handler.assert_called_once_with(clip_model_path=str(projector), verbose=False, use_gpu=False)
            kwargs = llama.call_args.kwargs
            self.assertEqual(kwargs["model_path"], str(model))
            self.assertIs(kwargs["chat_handler"], fake_handler)
            self.assertNotIn("chat_format", kwargs)

    def test_builtin_analysis_sends_image_and_forces_json_schema(self):
        with tempfile.TemporaryDirectory() as d:
            llm = MagicMock()
            llm.create_chat_completion.return_value = {
                "choices": [{"message": {"content": json.dumps(GOOD)}}]
            }
            with patch.object(pilot, "detect_engine", return_value=("llamacpp", "model + projector")), \
                    patch.object(pilot, "_get_llm", return_value=llm):
                result = pilot.analyze(Image.new("RGB", (60, 100)), "hash", "裙子", Path(d), "model")
            self.assertIsNotNone(result["analysis"])
            kwargs = llm.create_chat_completion.call_args.kwargs
            self.assertEqual(kwargs["response_format"], {"type": "json_object", "schema": pilot.SCHEMA})
            content = kwargs["messages"][1]["content"]
            self.assertTrue(any(part.get("type") == "image_url" for part in content))

    def test_prune_cache_removes_expired_and_oldest_entries(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            (cache / "expired.json").write_text("expired")
            (cache / "old.json").write_text("old")
            (cache / "new.json").write_text("new")
            import os
            os.utime(cache / "expired.json", (0, 0))
            os.utime(cache / "old.json", (1, 1))
            removed = pilot.prune_cache(cache, max_age_seconds=2, max_bytes=3)
            self.assertEqual(removed, 2)
            self.assertFalse((cache / "expired.json").exists())
            self.assertFalse((cache / "old.json").exists())
            self.assertTrue((cache / "new.json").exists())

    def test_exact_duplicates_and_chinese_names(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            Image.new("RGB", (20, 30), "red").save(p / "下載 (1).jpeg")
            (p / "下載 (23).jpeg").write_bytes((p / "下載 (1).jpeg").read_bytes())
            before = pilot.inventory(p)
            self.assertEqual(before[0]["sha256"], before[1]["sha256"])
            pilot.read_image(p / "下載 (1).jpeg")
            self.assertEqual(before, pilot.inventory(p))

    def test_broken_image_and_disguised_format(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "壞圖.jpg"
            p.write_bytes(b"not an image")
            with self.assertRaises(OSError):
                pilot.read_image(p)
            Image.new("RGB", (10, 10)).save(p, format="BMP")
            with self.assertRaises(ValueError):
                pilot.read_image(p)

    def test_orientation_and_transparency(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "rotated.jpg"
            image = Image.new("RGB", (20, 30), "red")
            exif = Image.Exif()
            exif[274] = 6
            image.save(p, exif=exif)
            self.assertEqual(pilot.read_image(p).size, (30, 20))
            p = Path(d) / "alpha.png"
            Image.new("RGBA", (10, 10), (255, 0, 0, 0)).save(p)
            self.assertEqual(pilot.read_image(p).getpixel((0, 0)), (255, 255, 255))

    def test_full_width_background_and_bottom_pixels_are_preserved(self):
        image = Image.new("RGB", (100, 100), "white")
        ImageDraw.Draw(image).rectangle((15, 0, 84, 99), fill="#dddddd")
        ImageDraw.Draw(image).line((0, 99, 99, 99), fill="red")
        before = image.tobytes()
        with tempfile.TemporaryDirectory() as d:
            item = {"analysis": copy.deepcopy(GOOD), "issues": []}
            pilot.make_previews(image, item, Path(d), 1)
            self.assertEqual(item["crop_pixel_box"], [0, 10, 100, 100])
            with Image.open(Path(d) / "01-crop.png") as crop:
                self.assertEqual(crop.size, (100, 90))
                self.assertEqual(crop.tobytes(), image.crop((0, 10, 100, 100)).tobytes())
                self.assertEqual(crop.getpixel((0, 0)), (255, 255, 255))
                self.assertEqual(crop.getpixel((99, 89)), (255, 0, 0))
        self.assertEqual(image.tobytes(), before)

    def test_partial_or_absent_head_is_not_cropped(self):
        data = {**GOOD, "full_head_visible": False, "face_cut_y": 0}
        self.assertEqual(pilot.top_only_crop_box(data, (1100, 1100)), (0, 0, 1100, 1100))
        # Even a contradictory model line cannot crop an image without a full head.
        data["face_cut_y"] = 100
        self.assertTrue(pilot.validate_analysis(data))
        self.assertEqual(pilot.top_only_crop_box(data, (1100, 1100)), (0, 0, 1100, 1100))

    def test_invalid_coordinates_and_types(self):
        for box in [[0, 0, 1001, 1000], [900, 0, 100, 1000], [True, 0, 500, 1000], [0, 0, 500]]:
            data = copy.deepcopy(GOOD)
            data["garment_box"] = box
            with self.assertRaises(ValueError):
                pilot.validate_analysis(data)
        data = copy.deepcopy(GOOD)
        data["run_shell"] = "bad"
        with self.assertRaises(ValueError):
            pilot.validate_analysis(data)
        for value in [-1, 1001, True, "100", 1.5]:
            with self.assertRaises(ValueError):
                pilot.validate_analysis({**GOOD, "face_cut_y": value})
        # The model no longer has authority to supply an arbitrary rectangle.
        with self.assertRaises(ValueError):
            pilot.validate_analysis({**GOOD, "crop_box": [100, 100, 800, 800]})

    def test_unsafe_crop_is_never_approved_by_valid_json(self):
        data = copy.deepcopy(GOOD)
        data["face_cut_y"] = 300
        self.assertTrue(pilot.validate_analysis(data))
        self.assertEqual(pilot.validate_analysis(GOOD), [])
        self.assertEqual(pilot.top_only_crop_box(data, (1100, 1100)), (0, 132, 1100, 1100))

    def test_unsafe_line_uses_fallback_without_a_second_model_call(self):
        bad = {**GOOD, "face_cut_y": 495}
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def request(payload):
                calls.append(payload)
                return answer(bad)
            result = pilot.analyze(Image.new("RGB", (100, 100)), "hash", "dress", Path(d), "model", request)
            self.assertEqual(len(calls), 1)
            self.assertTrue(result["issues"])
            self.assertEqual(pilot.top_only_crop_box(result["analysis"], (100, 100)), (0, 14, 100, 100))
            again = pilot.analyze(Image.new("RGB", (100, 100)), "hash", "dress", Path(d), "model", request)
            self.assertTrue(again["cache_hit"])
            self.assertEqual(len(calls), 1)

    def test_unsafe_half_body_line_uses_a_lower_face_preserving_fallback(self):
        data = {**GOOD, "image_type": "half_body", "garment_box": [200, 246, 800, 850], "face_cut_y": 489}
        # Do not fall all the way to the garment top (which would remove the
        # lower face in a half-body photo); blend a scaled model hint with a
        # garment-relative floor.
        self.assertEqual(pilot.top_only_crop_box(data, (1000, 1000)), (0, 196, 1000, 1000))

    def test_tiny_head_line_is_rejected_and_expanded(self):
        data = {**GOOD, "face_cut_y": 20}
        issues = pilot.validate_analysis(data)
        self.assertTrue(any("上半部未完整移除" in issue for issue in issues))
        self.assertEqual(pilot.top_only_crop_box(data, (1000, 1000)), (0, 88, 1000, 1000))

    def test_interrupted_safety_retry_can_resume_without_repeating_first_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            calls = []
            def request(payload):
                calls.append(payload)
                if len(calls) == 1:
                    return answer({**GOOD, "product_present": False})
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                pilot.analyze(Image.new("RGB", (100, 100)), "hash", "dress", cache, "model", request)
            resumed_calls = []
            def resume(payload):
                resumed_calls.append(payload)
                return answer()
            result = pilot.analyze(Image.new("RGB", (100, 100)), "hash", "dress", cache, "model", resume)
            self.assertEqual(len(resumed_calls), 1)
            self.assertEqual(len(result["attempts"]), 2)
            self.assertEqual(result["issues"], [])

    def test_cache_avoids_model_and_invalidates_on_product_or_model_change(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            image = Image.new("RGB", (60, 100), "gray")
            calls = []
            def request(payload):
                calls.append(payload)
                return answer()
            a = pilot.analyze(image, "hash", "裙子", cache, "model1", request)
            b = pilot.analyze(image, "hash", "裙子", cache, "model1", request)
            self.assertFalse(a["cache_hit"])
            self.assertTrue(b["cache_hit"])
            self.assertEqual(len(calls), 1)
            pilot.analyze(image, "hash", "褲子", cache, "model1", request)
            pilot.analyze(image, "hash", "裙子", cache, "model2", request)
            self.assertEqual(len(calls), 3)
            # Payload contains inline pixels, not a remote URL or an absolute source path.
            uri = calls[0]["input"][1]["data_url"]
            self.assertTrue(uri.startswith("data:image/jpeg;base64,"))
            self.assertNotIn("tools", calls[0])
            self.assertEqual(calls[0]["integrations"], [])
            self.assertFalse(calls[0]["store"])
            self.assertEqual(calls[0]["reasoning"], "off")

    def test_retry_at_most_once(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def request(payload):
                calls.append(payload)
                return {"output": []}
            result = pilot.analyze(Image.new("RGB", (10, 10)), "hash", "dress", Path(d), "model", request)
            self.assertEqual(len(calls), 2)
            self.assertIsNone(result["analysis"])
            self.assertEqual(len(result["attempts"]), 2)
            self.assertEqual(result["attempts"][0]["raw_response"], {"output": []})

    def test_service_failure_is_distinguished_from_bad_image_analysis(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def request(payload):
                calls.append(payload)
                raise pilot.ModelServiceUnavailable("Not running")
            result = pilot.analyze(Image.new("RGB", (10, 10)), "hash", "dress", Path(d), "model", request)
            self.assertEqual(len(calls), 2)
            self.assertTrue(result["service_unavailable"])

    def test_main_stops_on_unavailable_service_and_lists_unprocessed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            source = root / "原圖"
            source.mkdir()
            for name in ("圖一.png", "圖二.png"):
                Image.new("RGB", (20, 30), "red").save(source / name)
            model_file = root / "model.gguf"
            model_file.write_bytes(b"test-only fake model identity")
            failure = {"analysis": None, "issues": ["服務不可用"], "service_unavailable": True}
            argv = ["pilot", "--input", str(source), "--product", "裙子", "--select", "圖一.png", "--select", "圖二.png"]
            with patch.object(pilot, "ROOT", root), patch.object(pilot, "MODEL_FILE", model_file), patch.object(sys, "argv", argv), patch.object(pilot, "analyze", return_value=failure) as analyze:
                self.assertEqual(pilot.main(), 2)
            self.assertEqual(analyze.call_count, 1)
            reports = list((root / "outputs").glob("*/report.json"))
            report = json.loads(reports[0].read_text())
            self.assertEqual(report["status"], "service_unavailable")
            self.assertEqual(report["not_processed"], ["圖二.png"])
            self.assertTrue(report["originals_unchanged"])

    def test_truncated_and_tool_outputs_are_rejected_with_raw_evidence(self):
        for response in [
            {**answer(), "stats": {"total_output_tokens": 1200}},
            {"output": [{"type": "tool_call", "tool": "anything", "arguments": {}}]},
        ]:
            with tempfile.TemporaryDirectory() as d:
                result = pilot.analyze(Image.new("RGB", (10, 10)), "hash", "dress", Path(d), "model", lambda p: response)
                self.assertIsNone(result["analysis"])
                self.assertEqual(result["attempts"][0]["raw_response"], response)

    def test_low_resolution_diagnostic_is_not_upscaled(self):
        with tempfile.TemporaryDirectory() as d:
            image = Image.new("RGB", (60, 100), "gray")
            item = {"analysis": copy.deepcopy(GOOD), "issues": []}
            pilot.make_previews(image, item, Path(d), 1)
            with Image.open(Path(d) / "01-crop.png") as crop:
                self.assertEqual(crop.size, (60, 90))
            self.assertEqual(item["crop_size"], [60, 90])
            self.assertTrue(any("放大" in x for x in item["issues"]))

    def test_interruption_retains_completed_cache(self):
        with tempfile.TemporaryDirectory() as d:
            image, cache = Image.new("RGB", (10, 10)), Path(d)
            pilot.analyze(image, "done", "dress", cache, "model", lambda p: answer())
            def interrupted(payload):
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                pilot.analyze(image, "next", "dress", cache, "model", interrupted)
            reused = pilot.analyze(image, "done", "dress", cache, "model", interrupted)
            self.assertTrue(reused["cache_hit"])

    def test_fixed_local_network_and_redirect_rejection(self):
        captured = {}
        class Opener:
            def open(self, request, timeout):
                captured["url"] = request.full_url
                return io.BytesIO(json.dumps(answer()).encode())
        def build(*handlers):
            self.assertTrue(any(isinstance(x, pilot.urllib.request.ProxyHandler) and x.proxies == {} for x in handlers))
            self.assertTrue(any(isinstance(x, pilot.RejectRedirect) for x in handlers))
            return Opener()
        with patch.object(pilot.urllib.request, "build_opener", side_effect=build):
            pilot.local_request({})
        self.assertEqual(captured["url"], "http://127.0.0.1:1234/api/v1/chat")
        with self.assertRaises(RuntimeError):
            pilot.RejectRedirect().redirect_request(None, None, 302, "", {}, "https://example.com")

    def test_analyze_uses_1024_preview_not_640(self):
        with tempfile.TemporaryDirectory() as d:
            captured = {}
            def request(payload):
                data_url = payload["input"][1]["data_url"]
                import base64
                raw = base64.b64decode(data_url.split(",")[1])
                from PIL import Image as PILImage
                img = PILImage.open(io.BytesIO(raw))
                captured["preview_size"] = img.size
                return answer()
            pilot.analyze(Image.new("RGB", (4000, 3000)), "hash", "dress", Path(d), "model", request)
            w, h = captured["preview_size"]
            self.assertLessEqual(max(w, h), 1024)

    def test_cache_includes_model_identity(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            calls = []
            def request(payload):
                calls.append(payload)
                return answer()
            pilot.analyze(Image.new("RGB", (60, 100)), "hash", "裙子", cache, "model-v1", request)
            pilot.analyze(Image.new("RGB", (60, 100)), "hash", "裙子", cache, "model-v2", request)
            self.assertEqual(len(calls), 2)

    def test_report_escapes_user_and_model_content(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            report = {"product": "<script>alert(1)</script>", "model": "local", "inventory": [],
                      "duplicates": [], "status": "awaiting_visual_review", "results": [
                          {"name": "<img src=x onerror=alert(1)>", "analysis": GOOD, "issues": []}]}
            pilot.write_report(p, report)
            text = (p / "index.html").read_text()
            self.assertNotIn("<script>", text)
            self.assertIn("&lt;script&gt;", text)
            self.assertIn("default-src 'none'", text)


if __name__ == "__main__":
    unittest.main()
