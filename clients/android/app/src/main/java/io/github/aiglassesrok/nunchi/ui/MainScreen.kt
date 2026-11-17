package io.github.aiglassesrok.nunchi.ui

import android.app.Activity
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CameraAlt
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import io.github.aiglassesrok.nunchi.ChatMessage
import io.github.aiglassesrok.nunchi.HubSettings
import io.github.aiglassesrok.nunchi.MainViewModel
import io.github.aiglassesrok.nunchi.Role

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(viewModel: MainViewModel, activity: Activity) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    var input by remember { mutableStateOf("") }
    var showSettings by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()

    // 답이 스트리밍으로 길어지는 동안에도 마지막 말풍선의 끝이 보이게 따라 내려감
    val last = ui.messages.lastOrNull()
    LaunchedEffect(ui.messages.size, last?.text?.length, last?.sources?.size, last?.meta) {
        if (ui.messages.isNotEmpty()) listState.animateScrollToItem(ui.messages.lastIndex, scrollOffset = 100_000)
    }

    MaterialTheme(colorScheme = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = {
                        Column {
                            Text("Nunchi")
                            Text(
                                "허브: ${ui.hubStatus} · 안경: ${ui.glassesStatus}" + if (ui.virtualGlasses) " (가상)" else "",
                                style = MaterialTheme.typography.labelSmall,
                                maxLines = 2,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    },
                    actions = {
                        TextButton(onClick = viewModel::resetConversation) { Text("초기화") }
                        IconButton(onClick = { showSettings = true }) { Icon(Icons.Filled.Settings, contentDescription = "설정") }
                    },
                )
            },
        ) { padding ->
            Column(Modifier.fillMaxSize().padding(padding).imePadding()) {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 12.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    items(ui.messages, key = { it.id }) { MessageRow(it) }
                }
                Row(
                    Modifier.fillMaxWidth().padding(12.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    OutlinedButton(
                        onClick = { viewModel.askWithGlassesPhoto(input); input = "" },
                        enabled = !ui.busy && !ui.recording,
                    ) { Icon(Icons.Filled.CameraAlt, contentDescription = "안경으로 사진 찍어 묻기") }
                    OutlinedTextField(
                        value = input,
                        onValueChange = { input = it },
                        modifier = Modifier.weight(1f),
                        placeholder = { Text("질문 입력") },
                        singleLine = true,
                        trailingIcon = {
                            TextButton(onClick = { viewModel.ask(input); input = "" }, enabled = input.isNotBlank()) { Text("보내기") }
                        },
                    )
                    FilledIconButton(
                        onClick = viewModel::toggleRecording,
                        enabled = !ui.busy || ui.recording,
                        modifier = Modifier.size(64.dp),
                        colors = if (ui.recording) IconButtonDefaults.filledIconButtonColors(containerColor = Color(0xFFD23A3A)) else IconButtonDefaults.filledIconButtonColors(),
                    ) {
                        Icon(
                            if (ui.recording) Icons.Filled.Stop else Icons.Filled.Mic,
                            contentDescription = if (ui.recording) "녹음 끝내고 보내기" else "눌러서 말하기",
                        )
                    }
                }
            }
        }

        if (showSettings) {
            SettingsDialog(
                current = ui.settings,
                glassesStatus = ui.glassesStatus,
                deviceCount = ui.deviceCount,
                virtualOn = ui.virtualGlasses,
                onVirtual = viewModel::setVirtualGlasses,
                onRegister = { viewModel.registerGlasses(activity) },
                onUnregister = { viewModel.unregisterGlasses(activity) },
                onSave = { viewModel.saveSettings(it); showSettings = false },
                onDismiss = { showSettings = false },
            )
        }
    }
}

@Composable
private fun MessageRow(m: ChatMessage) {
    val isUser = m.role == Role.USER
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
        Surface(
            color = when (m.role) {
                Role.USER -> MaterialTheme.colorScheme.primary
                Role.ASSISTANT -> MaterialTheme.colorScheme.surfaceVariant
                Role.NOTICE -> MaterialTheme.colorScheme.surface
            },
            contentColor = if (isUser) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface,
            shape = RoundedCornerShape(14.dp),
            modifier = Modifier.widthIn(max = 320.dp),
        ) {
            Column(Modifier.padding(10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                m.image?.let { Image(bitmap = it, contentDescription = "안경 사진", modifier = Modifier.widthIn(max = 220.dp)) }
                Text(m.text.ifEmpty { "…" })
                if (m.sources.isNotEmpty()) {
                    Text("참고 문서: " + m.sources.joinToString(", "), style = MaterialTheme.typography.labelSmall)
                }
                if (m.meta.isNotBlank()) Text(m.meta.trim(), style = MaterialTheme.typography.labelSmall)
            }
        }
    }
}

@Composable
private fun SettingsDialog(
    current: HubSettings,
    glassesStatus: String,
    deviceCount: Int,
    virtualOn: Boolean,
    onVirtual: (Boolean) -> Unit,
    onRegister: () -> Unit,
    onUnregister: () -> Unit,
    onSave: (HubSettings) -> Unit,
    onDismiss: () -> Unit,
) {
    var url by remember { mutableStateOf(current.hubUrl) }
    var token by remember { mutableStateOf(current.token) }
    var glassesMic by remember { mutableStateOf(current.useGlassesMic) }
    var voice by remember { mutableStateOf(current.voice) }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("설정") },
        text = {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                item {
                    OutlinedTextField(url, { url = it }, label = { Text("허브 주소") }, singleLine = true)
                }
                item {
                    OutlinedTextField(token, { token = it }, label = { Text("토큰 (NUNCHI_TOKEN)") }, singleLine = true)
                }
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("안경 마이크 사용", Modifier.weight(1f))
                        Switch(checked = glassesMic, onCheckedChange = { glassesMic = it })
                    }
                }
                item { Text("답 읽기", style = MaterialTheme.typography.labelLarge) }
                items(listOf("phone" to "폰이 읽기 (권장)", "hub" to "허브 음성", "off" to "읽지 않기")) { (key, label) ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(selected = voice == key, onClick = { voice = key })
                        Text(label)
                    }
                }
                item {
                    Column(
                        Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp)).padding(8.dp),
                        verticalArrangement = Arrangement.spacedBy(4.dp),
                    ) {
                        Text("안경: $glassesStatus · 주변 $deviceCount 개", style = MaterialTheme.typography.labelMedium)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = onRegister) { Text("Meta AI와 연결") }
                            OutlinedButton(onClick = onUnregister) { Text("해제") }
                        }
                        Text("Meta AI 앱 > 설정 > 내 안경 > 개발자 모드를 먼저 켜세요. 등록은 처음 한 번 인터넷이 필요해요.", style = MaterialTheme.typography.labelSmall)
                    }
                }
                item {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("가상 안경 (개발용)")
                            Text("실물 없이 등록·세션·카메라를 시험. 사진은 예시 계기판(E2).", style = MaterialTheme.typography.labelSmall)
                        }
                        Switch(checked = virtualOn, onCheckedChange = onVirtual)
                    }
                }
                item { Spacer(Modifier.size(4.dp)) }
            }
        },
        confirmButton = { TextButton(onClick = { onSave(HubSettings(url, token, glassesMic, voice)) }) { Text("저장") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("닫기") } },
    )
}
