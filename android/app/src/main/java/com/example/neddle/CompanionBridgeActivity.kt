package com.example.neddle

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.FileProvider
import java.io.File

class CompanionBridgeActivity : AppCompatActivity() {

    private lateinit var webView: WebView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }

        val urlBar = EditText(this).apply {
            setText("http://127.0.0.1:8000")
            hint = "Enter local Playground / Claude Bridge URL"
        }

        val connectBtn = Button(this).apply {
            text = "Connect to Local AI Bridge"
            setOnClickListener {
                webView.loadUrl(urlBar.text.toString().trim())
            }
        }

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            webViewClient = WebViewClient()
            addJavascriptInterface(DeviceBridgeInterface(), "AndroidBridge")
            layoutParams = LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                0,
                1f
            )
        }

        root.addView(urlBar)
        root.addView(connectBtn)
        root.addView(webView)
        setContentView(root)

        webView.loadUrl(urlBar.text.toString())
    }

    inner class DeviceBridgeInterface {
        @JavascriptInterface
        fun installApk(filePath: String): String {
            val apkFile = File(filePath)
            if (!apkFile.exists()) {
                return "APK not found at $filePath"
            }
            val uri: Uri = FileProvider.getUriForFile(
                this@CompanionBridgeActivity,
                "$packageName.fileprovider",
                apkFile
            )
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(intent)
            return "Install prompt launched for $filePath"
        }

        @JavascriptInterface
        fun launchPackage(targetPackage: String): String {
            val launchIntent = packageManager.getLaunchIntentForPackage(targetPackage)
            return if (launchIntent != null) {
                startActivity(launchIntent)
                "Launched $targetPackage"
            } else {
                Toast.makeText(this@CompanionBridgeActivity, "Package not found: $targetPackage", Toast.LENGTH_SHORT).show()
                "Package not found: $targetPackage"
            }
        }
    }
}
