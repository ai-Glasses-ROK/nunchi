import java.util.Properties
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.jetbrains.kotlin.android)
    alias(libs.plugins.compose.compiler)
}

// local.properties 에 mwdat_application_id / mwdat_client_token 을 적으면 배포용 값으로 바뀝니다.
// 개발자 모드에서는 비워 두거나 0 이면 됩니다.
val localProperties = Properties().apply {
    val file = rootDir.resolve("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

android {
    namespace = "io.github.aiglassesrok.nunchi"
    compileSdk = 36

    defaultConfig {
        applicationId = "io.github.aiglassesrok.nunchi"
        minSdk = 31 // Android 12+: 블루투스 마이크 경로 지정(setCommunicationDevice) API
        targetSdk = 36
        versionCode = 1
        versionName = "0.1.0"
        manifestPlaceholders["mwdat_application_id"] = localProperties.getProperty("mwdat_application_id", "0")
        manifestPlaceholders["mwdat_client_token"] = localProperties.getProperty("mwdat_client_token", "0")
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    buildFeatures {
        compose = true
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging {
        resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" }
        // 폰 LLM 엔진(LiteRT-LM)은 ARM64 폰·애플 실리콘 에뮬레이터용만 넣음 (x86_64판 25MB 절약, 없으면 앱이 허브·추출형으로 대신)
        jniLibs { excludes += "**/x86_64/liblitertlm_jni.so" }
    }
    testOptions { unitTests.isReturnDefaultValues = true }
}

kotlin { compilerOptions { jvmTarget = JvmTarget.JVM_17 } }

dependencies {
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.mwdat.core)
    implementation(libs.mwdat.camera)
    implementation(libs.mwdat.mockdevice) // 가상 안경 (실물 없이 테스트)
    implementation(libs.litertlm.android) // 폰 단독 모드의 기기 내 LLM (모델 파일이 있을 때만 사용)
    implementation(libs.uvcandroid) // 드론 카메라: 5.8GHz FPV 수신기(UVC)

    testImplementation(libs.junit)
    testImplementation(libs.org.json) // JVM 테스트에서는 안드로이드 org.json이 비어 있어서
    testImplementation(libs.dronefleet.mavlink) // MAVLink 파서 교차 검증용 (앱에는 안 들어감)
}

// ---------------------------------------------------------------- 폰 단독 모드의 기본 지식베이스
// 레포의 examples/knowledge(예제 문서 3개)와 examples/drone(비행컨트롤러 점검 안내)을 APK의 assets/kb/ 로 넣습니다.
// 파일을 복사해 두지 않고 빌드할 때 가져오므로, 예제 문서를 고치면 앱에도 그대로 반영됩니다.
abstract class KnowledgeAssetsTask : DefaultTask() {
    @get:InputFiles
    @get:PathSensitive(PathSensitivity.RELATIVE)
    abstract val sourceFiles: ConfigurableFileCollection

    @get:OutputDirectory
    abstract val outputDir: DirectoryProperty

    @TaskAction
    fun copy() {
        val out = outputDir.get().asFile.resolve("kb")
        out.deleteRecursively()
        out.mkdirs()
        sourceFiles.files.filter { it.isFile }.sortedBy { it.name }.forEach { it.copyTo(out.resolve(it.name), overwrite = true) }
    }
}

val examplesDir = rootDir.resolve("../../examples")
val knowledgeAssets = tasks.register<KnowledgeAssetsTask>("knowledgeAssets") {
    sourceFiles.from(
        fileTree(examplesDir.resolve("knowledge")) { include("*.md") },
        fileTree(examplesDir.resolve("drone")) { include("*.md") },
    )
}

androidComponents {
    onVariants { variant ->
        variant.sources.assets?.addGeneratedSourceDirectory(knowledgeAssets, KnowledgeAssetsTask::outputDir)
    }
}
