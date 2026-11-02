// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RemoteFeature",
    products: [.library(name: "RemoteFeature", targets: ["RemoteFeature"])],
    targets: [.target(name: "RemoteFeature")]
)
