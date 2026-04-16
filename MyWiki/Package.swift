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
    dependencies: [
        .package(url: "https://github.com/gonzalezreal/swift-markdown-ui", exact: "2.4.1"),
    ],
    targets: [
        .target(
            name: "MyWikiCore",
            path: "Sources/MyWikiCore"
        ),
        .executableTarget(
            name: "MyWikiApp",
            dependencies: [
                "MyWikiCore",
                .product(name: "MarkdownUI", package: "swift-markdown-ui"),
            ],
            path: "Sources/MyWikiApp",
            resources: [.copy("Resources/AppIcon.icns")]
        ),
        .testTarget(
            name: "MyWikiCoreTests",
            dependencies: ["MyWikiCore"],
            path: "Tests/MyWikiCoreTests"
        ),
        .testTarget(
            name: "MyWikiAppTests",
            dependencies: ["MyWikiApp"],
            path: "Tests/MyWikiAppTests"
        ),
    ]
)
