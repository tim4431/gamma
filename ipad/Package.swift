// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "GammaCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [.library(name: "GammaCore", targets: ["GammaCore"])],
    targets: [
        .systemLibrary(name: "CSQLite", path: "CSQLite", pkgConfig: "sqlite3"),
        .target(name: "GammaCore", dependencies: ["CSQLite"], path: "GammaCore"),
        .testTarget(name: "GammaCoreTests", dependencies: ["GammaCore"], path: "GammaCoreTests", resources: [.copy("Fixtures")])
    ]
)
