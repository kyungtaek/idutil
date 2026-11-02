import Foundation

public enum LocalFeature {
    public static func value() -> String { "local-v1" }
    public static func resource() -> String {
        guard let url = Bundle.module.url(forResource: "value", withExtension: "txt"),
              let content = try? String(contentsOf: url, encoding: .utf8) else {
            return "RESOURCE_MISSING"
        }
        return content.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
