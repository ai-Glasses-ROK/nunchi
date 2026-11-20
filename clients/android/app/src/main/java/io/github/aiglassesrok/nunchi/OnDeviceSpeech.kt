package io.github.aiglassesrok.nunchi

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import kotlin.coroutines.resume
import kotlinx.coroutines.CancellableContinuation

/**
 * 폰 단독 모드의 받아쓰기: 안드로이드 12+의 '기기 내' 음성 인식기만 씁니다 (음성이 폰 밖으로 나가지 않음).
 * 한국어 기기 내 인식 데이터가 없으면 [available]이 false → 글자로 묻거나 허브 모드를 쓰세요.
 * 모든 함수는 메인 스레드에서 부릅니다(SpeechRecognizer 규칙).
 */
class OnDeviceSpeech(private val context: Context) {
    private var recognizer: SpeechRecognizer? = null
    private var pending: CancellableContinuation<String>? = null

    val available: Boolean get() = SpeechRecognizer.isOnDeviceRecognitionAvailable(context)

    /** 듣기 시작. 결과는 [stop]을 부른 뒤(또는 말이 끝나 자동으로) [result]로 받습니다. */
    fun start() {
        cancel()
        val r = SpeechRecognizer.createOnDeviceSpeechRecognizer(context)
        r.setRecognitionListener(object : RecognitionListener {
            override fun onResults(results: Bundle) {
                finish(results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull().orEmpty())
            }
            override fun onError(error: Int) = finish("")
            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() = Unit
            override fun onPartialResults(partialResults: Bundle?) = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit
        })
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            .putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ko-KR")
            .putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, true)
        r.startListening(intent)
        recognizer = r
        lastResult = null
    }

    private var lastResult: String? = null

    private fun finish(text: String) {
        lastResult = text
        pending?.let { if (it.isActive) it.resume(text) }
        pending = null
        recognizer?.destroy()
        recognizer = null
    }

    fun stop() {
        recognizer?.stopListening()
    }

    suspend fun result(): String {
        lastResult?.let { return it }
        return kotlinx.coroutines.suspendCancellableCoroutine { cont ->
            pending = cont
            cont.invokeOnCancellation { cancel() }
        }
    }

    fun cancel() {
        recognizer?.destroy()
        recognizer = null
        pending = null
    }
}
