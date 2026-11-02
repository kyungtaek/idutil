// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "LocalFeature",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [.library(name: "LocalFeature", targets: ["LocalFeature"])],
    targets: [.target(name: "LocalFeature", resources: [.process("Resources")])]
)
