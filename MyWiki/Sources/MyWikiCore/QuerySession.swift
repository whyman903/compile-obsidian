import Foundation
import Observation

@MainActor
@Observable
public final class QuerySession {
    public enum Status: Equatable, Sendable {
        case idle
        case running
        case completed
        case failed
        case cancelled
    }

    public private(set) var status: Status = .idle
    public private(set) var question: String = ""
    public private(set) var assistantText: String = ""
    public private(set) var toolCalls: [String] = []
    public private(set) var errorMessage: String?
    public private(set) var costUSD: Double?
    public private(set) var durationMs: Int?
    public private(set) var permissionDenials: [String] = []
    public private(set) var startedAt: Date?
    public private(set) var statusDetail: String = ""

    public init() {}

    public func start(question: String) {
        self.question = question
        self.status = .running
        self.assistantText = ""
        self.toolCalls = []
        self.errorMessage = nil
        self.costUSD = nil
        self.durationMs = nil
        self.permissionDenials = []
        self.startedAt = Date()
        self.statusDetail = ""
    }

    public func updateStatusDetail(_ detail: String) {
        self.statusDetail = detail
    }

    public func handle(_ event: ClaudeQueryEvent) {
        switch event {
        case .assistantText(let text):
            self.assistantText = text
        case .toolCall(let name):
            self.toolCalls.append(name)
            switch name {
            case "Grep": self.statusDetail = "Searching the wiki…"
            case "Read": self.statusDetail = "Reading a page…"
            case "Glob": self.statusDetail = "Listing pages…"
            case "LS": self.statusDetail = "Walking directories…"
            case "WebSearch", "WebFetch": self.statusDetail = "Checking the web…"
            default: self.statusDetail = "Using \(name)…"
            }
        case .toolResult:
            break
        case .finished(let text, let cost, let duration, let denials):
            if !text.isEmpty {
                self.assistantText = text
            }
            self.costUSD = cost
            self.durationMs = duration
            self.permissionDenials = denials
            self.status = .completed
        case .failed(let message):
            self.errorMessage = message
            self.status = .failed
        }
    }

    public func fail(_ message: String) {
        self.errorMessage = message
        self.status = .failed
    }

    public func cancel() {
        self.status = .cancelled
    }

    public func clear() {
        self.status = .idle
        self.question = ""
        self.assistantText = ""
        self.toolCalls = []
        self.errorMessage = nil
        self.costUSD = nil
        self.durationMs = nil
        self.permissionDenials = []
        self.startedAt = nil
        self.statusDetail = ""
    }
}
