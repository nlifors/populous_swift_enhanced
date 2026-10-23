// swift-tools-version: 6.0
import PackageDescription

// Command Line Tools (no Xcode) does not put the Swift Testing macro plugin on the
// default search path.
let testSettings: [SwiftSetting] = [
    .unsafeFlags(["-plugin-path", "/Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing"])
]

let package = Package(
    name: "Populous",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "PopulousAssets", targets: ["PopulousAssets"]),
        .library(name: "PopulousCore", targets: ["PopulousCore"]),
        .executable(name: "popassets", targets: ["popassets"]),
        .executable(name: "Populous", targets: ["Populous"]),
    ],
    targets: [
        .target(name: "PopulousAssets"),
        .target(name: "PopulousCore"),
        .target(name: "PopulousRender", dependencies: ["PopulousAssets", "PopulousCore"]),
        .target(name: "PopulousAudio", dependencies: ["PopulousAssets"]),
        .target(
            name: "PopulousHD", dependencies: ["PopulousAssets", "PopulousCore", "PopulousRender"],
            resources: [.copy("Resources/Models"), .copy("Resources/Textures")]),
        .executableTarget(
            name: "popassets", dependencies: ["PopulousAssets", "PopulousCore", "PopulousRender", "PopulousAudio"]),
        .executableTarget(
            name: "Populous",
            dependencies: ["PopulousAssets", "PopulousCore", "PopulousRender", "PopulousAudio", "PopulousHD"]),
        .testTarget(name: "PopulousAssetsTests", dependencies: ["PopulousAssets"], swiftSettings: testSettings),
        .testTarget(name: "PopulousCoreTests", dependencies: ["PopulousCore"], swiftSettings: testSettings),
        .testTarget(name: "PopulousAudioTests", dependencies: ["PopulousAudio"], swiftSettings: testSettings),
        .testTarget(
            name: "PopulousRenderTests", dependencies: ["PopulousAssets", "PopulousCore", "PopulousRender", "PopulousHD"],
            swiftSettings: testSettings),
    ]
)
