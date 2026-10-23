plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// the app version follows the web app / bridge version (keep in step with VERSION in rtk_bridge.py)
val appVersion = "2.1.0"

android {
    namespace = "us.parcelrtk.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "us.parcelrtk.app"
        minSdk = 26
        targetSdk = 35
        versionCode = 2100
        versionName = appVersion
    }
    sourceSets {
        // the web app itself is packaged straight from the repo's web/ folder
        getByName("main").assets.srcDirs("src/main/assets", "../../web-assets")
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
    buildFeatures { buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    packaging { resources.excludes += setOf("META-INF/*.md", "META-INF/LICENSE*") }
}

// Copy web/ into web-assets/web so the asset loader serves it at /assets/web/
val stageWeb by tasks.registering(Sync::class) {
    from(layout.projectDirectory.dir("../../web"))
    into(layout.projectDirectory.dir("../../web-assets/web"))
}
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach { dependsOn(stageWeb) }
tasks.matching { it.name == "preBuild" }.configureEach { dependsOn(stageWeb) }

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.activity:activity-ktx:1.9.3")
    implementation("androidx.webkit:webkit:1.12.1")
}
