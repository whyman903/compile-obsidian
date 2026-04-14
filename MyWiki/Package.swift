// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MyWiki",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .library(name: "MyWikiCore", targets: ["MyWikiCore"]),
        .executable(name: "MyWiki", targets: ["MyWikiApp"]),
    ],
    targets: [
        .target(
            name: "MyWikiCore",
            path: "Sources/MyWikiCore"
        ),
        .executableTarget(
            name: "MyWikiApp",
            dependencies: ["MyWikiCore"],
            path: "Sources/MyWikiApp"
        ),
        .testTarget(
            name: "MyWikiCoreTests",
            dependencies: ["MyWikiCore"],
            path: "Tests/MyWikiCoreTests"
        ),
    ]
)
