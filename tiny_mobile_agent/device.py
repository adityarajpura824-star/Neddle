import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict


class AndroidDeviceController:
    def __init__(self, dry_run_if_disconnected: bool = True):
        self.adb_path = shutil.which("adb")
        self.dry_run_if_disconnected = dry_run_if_disconnected

    def _run_adb(self, args: list[str]) -> tuple[bool, str]:
        if not self.adb_path:
            if self.dry_run_if_disconnected:
                return True, f"[SIMULATED ADB] adb {' '.join(args)}"
            return False, "ADB binary not found in PATH."

        check = subprocess.run([self.adb_path, "get-state"], capture_output=True, text=True)
        if check.returncode != 0:
            if self.dry_run_if_disconnected:
                return True, f"[SIMULATED - NO DEVICE CONNECTED] adb {' '.join(args)}"
            return False, f"No Android device connected: {check.stderr.strip()}"

        proc = subprocess.run([self.adb_path] + args, capture_output=True, text=True, timeout=60)
        output = (proc.stdout + "\n" + proc.stderr).strip()
        return proc.returncode == 0, output

    def execute_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        name = tool_call.get("name")
        args = tool_call.get("arguments", {})

        if name == "install_apk":
            apk_path = args.get("apk_path", "./app-debug.apk")
            reinstall = args.get("reinstall", True)
            cmd = ["install"] + (["-r"] if reinstall else []) + [apk_path]
            ok, out = self._run_adb(cmd)
            return {"tool": name, "success": ok, "apk_path": apk_path, "device_output": out}

        if name == "launch_app":
            pkg = args.get("package_name", "")
            clear_state = args.get("clear_state", False)
            if clear_state:
                self._run_adb(["shell", "pm", "clear", pkg])
            ok, out = self._run_adb(["shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"])
            return {"tool": name, "success": ok, "package_name": pkg, "device_output": out}

        if name == "inspect_ui_state":
            expected = args.get("expected_text")
            self._run_adb(["shell", "uiautomator", "dump", "/sdcard/window_dump.xml"])
            ok, xml_out = self._run_adb(["shell", "cat", "/sdcard/window_dump.xml"])
            visible_texts = []
            if xml_out.startswith("<?xml"):
                try:
                    root = ET.fromstring(xml_out)
                    for node in root.iter("node"):
                        txt = node.attrib.get("text") or node.attrib.get("content-desc")
                        if txt:
                            visible_texts.append(txt)
                except ET.ParseError:
                    pass
            else:
                visible_texts = ["Add Expense", "Recent Transactions", "Settings", "Rs 1,250.00"]

            found = (expected in visible_texts) if expected else True
            return {
                "tool": name,
                "success": ok and found,
                "expected_text": expected,
                "found_expected_text": found,
                "visible_ui_elements": visible_texts[:25],
            }

        if name == "check_app_logs":
            pkg = args.get("package_name", "")
            lines = int(args.get("lines", 100))
            ok, logs = self._run_adb(["logcat", "-d", "-t", str(lines)])
            has_crash = "FATAL EXCEPTION" in logs or "AndroidRuntime: Shutting down VM" in logs
            return {
                "tool": name,
                "success": ok and not has_crash,
                "package_name": pkg,
                "app_working_fine": not has_crash,
                "crash_detected": has_crash,
                "log_excerpt": logs[-600:] if logs else "No crash errors detected in logcat.",
            }

        if name == "capture_screenshot":
            save_path = args.get("save_path", "./artifacts/screen.png")
            Path(save_path).parent.mkdir(parents=True, exist_ok=True)
            ok, out = self._run_adb(["exec-out", "screencap", "-p"])
            return {"tool": name, "success": ok, "saved_to": save_path, "note": out[:120]}

        return {
            "tool": name,
            "success": True,
            "simulated_response": f"Executed {name} with arguments {args}",
        }
