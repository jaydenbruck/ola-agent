// swift-tools-version: 5.9
import PackageDescription

let package = Package(name: "OlaCore", products: [.library(name: "OlaCore", targets: ["OlaCore"])], targets: [
    .target(name: "OlaCore", path: "Ola", exclude: ["OlaApp.swift", "Client.swift", "Audio.swift", "Views.swift", "Takeover.swift", "Info.plist", "de.lproj", "en.lproj", "Assets.xcassets"], sources: ["Core.swift"]),
    .testTarget(name: "OlaCoreTests", dependencies: ["OlaCore"], path: "Tests")
])
