// Tools Claude sees through the connector. The phone app implements each of them.
// Kept static here so Claude can list tools even while the phone is offline.

const noArgs = { type: "object", properties: {}, additionalProperties: false };

export const TOOLS = [
  {
    name: "get_device_status",
    title: "Phone status",
    description:
      "Report the phone's model, Android version, battery level, and whether the Neddle accessibility service " +
      "(needed for screen inspection and screenshots) and log access are enabled. Call this first to check the phone is reachable.",
    inputSchema: noArgs,
    annotations: { readOnlyHint: true },
  },
  {
    name: "launch_app",
    title: "Launch app",
    description:
      "Open an installed Android app by its package name (for example com.example.expensetracker). " +
      "Android does not let an app clear another app's data, so this only launches it.",
    inputSchema: {
      type: "object",
      properties: {
        package_name: { type: "string", description: "Android package identifier, e.g. com.example.expensetracker" },
      },
      required: ["package_name"],
      additionalProperties: false,
    },
  },
  {
    name: "install_apk",
    title: "Install APK",
    description:
      "Download an APK from a URL (or use a file the app can read) and open Android's installer. " +
      "Android requires the user to tap Install on the phone, so the result says the installer was opened, not that the install finished. " +
      "Follow up with launch_app or get_device_status to confirm.",
    inputSchema: {
      type: "object",
      properties: {
        apk_url: { type: "string", description: "HTTPS URL of the .apk file" },
        apk_path: { type: "string", description: "Path of an .apk file the Neddle app can read on the phone" },
      },
      additionalProperties: false,
    },
  },
  {
    name: "inspect_ui_state",
    title: "Inspect screen",
    description:
      "Read the text and labels of every visible element on the phone's current screen. " +
      "Optionally pass expected_text to check whether that text is on screen. Needs the Neddle accessibility service turned on.",
    inputSchema: {
      type: "object",
      properties: {
        expected_text: { type: "string", description: "Optional text or label that should be visible" },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
  },
  {
    name: "capture_screenshot",
    title: "Take screenshot",
    description:
      "Capture the phone's current screen and return it as an image so you can see it. Needs Android 11 or newer and the Neddle accessibility service.",
    inputSchema: noArgs,
    annotations: { readOnlyHint: true },
  },
  {
    name: "check_app_logs",
    title: "Check app logs",
    description:
      "Look for crashes in an app. If the one-time READ_LOGS permission was granted it reads logcat for the package; " +
      "otherwise it reports what it can see (app running, crash or not-responding dialogs on screen).",
    inputSchema: {
      type: "object",
      properties: {
        package_name: { type: "string", description: "Android package identifier to check" },
        lines: { type: "integer", description: "How many recent log lines to inspect (default 100)" },
      },
      required: ["package_name"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
  },
];
