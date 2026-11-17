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
    packaging { resources { excludes += "/META-INF/{AL2.0,LGPL2.1}" } }
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
}
