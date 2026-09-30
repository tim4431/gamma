import Foundation

/// JSON in and out of the JavaScript core and the store. Values are what
/// `JSONSerialization` produces: dictionaries, arrays, strings, numbers,
/// booleans and `NSNull`.
enum JSON {
    struct Failure: Error, LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    static func string(_ value: Any?) throws -> String {
        let object = value ?? NSNull()
        // JSONSerialization raises (an Objective-C exception, not an error) on
        // what JSON cannot hold, a NaN or a Date: refuse it first
        let fragment = object is String || object is NSNumber || object is NSNull
        if let n = object as? Double, !n.isFinite { throw Failure(message: "a number JSON cannot hold") }
        guard fragment || JSONSerialization.isValidJSONObject(object) else { throw Failure(message: "not JSON") }
        let data = try JSONSerialization.data(withJSONObject: object, options: [.fragmentsAllowed, .withoutEscapingSlashes])
        guard let text = String(data: data, encoding: .utf8) else { throw Failure(message: "JSON is not UTF-8") }
        return text
    }

    static func parse(_ text: String) throws -> Any? {
        guard let data = text.data(using: .utf8) else { throw Failure(message: "not UTF-8") }
        let value = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
        return value is NSNull ? nil : value
    }

    static func parse(_ data: Data) -> Any? {
        guard let value = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else { return nil }
        return value is NSNull ? nil : value
    }

    /// A string literal of `text`, for JSON assembled around stored JSON text.
    static func quote(_ text: String) -> String {
        (try? string(text)) ?? "\"\""
    }
}

extension Dictionary where Key == String, Value == Any {
    func string(_ key: String) -> String { self[key] as? String ?? "" }
    func dict(_ key: String) -> [String: Any] { self[key] as? [String: Any] ?? [:] }
    func array(_ key: String) -> [Any] { self[key] as? [Any] ?? [] }
    func double(_ key: String) -> Double? { (self[key] as? NSNumber)?.doubleValue }
    func int(_ key: String) -> Int? { (self[key] as? NSNumber)?.intValue }
}
