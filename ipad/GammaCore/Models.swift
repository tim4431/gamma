import Foundation

public enum JSONValue: Codable, Equatable, Sendable {
    case null, bool(Bool), number(Double), string(String), array([JSONValue]), object([String: JSONValue])
    public init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() { self = .null }
        else if let v = try? c.decode(Bool.self) { self = .bool(v) }
        else if let v = try? c.decode(Double.self) { self = .number(v) }
        else if let v = try? c.decode(String.self) { self = .string(v) }
        else if let v = try? c.decode([JSONValue].self) { self = .array(v) }
        else { self = .object(try c.decode([String: JSONValue].self)) }
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .null: try c.encodeNil()
        case .bool(let v): try c.encode(v)
        case .number(let v): try c.encode(v)
        case .string(let v): try c.encode(v)
        case .array(let v): try c.encode(v)
        case .object(let v): try c.encode(v)
        }
    }
    public var string: String? { if case .string(let v) = self { return v }; return nil }
    public var number: Double? { if case .number(let v) = self { return v }; return nil }
    public var object: [String: JSONValue]? { if case .object(let v) = self { return v }; return nil }
    public var array: [JSONValue]? { if case .array(let v) = self { return v }; return nil }
    public subscript(_ key: String) -> JSONValue? { object?[key] }
}

public struct GammaBlock: Codable, Equatable, Identifiable, Sendable {
    public var id: String
    public var parent: String
    public var position: String
    public var content: String
    public var properties: [String: JSONValue]
    public init(id: String = GammaID.make(), parent: String, position: String = "a0", content: String = "", properties: [String: JSONValue] = [:]) {
        self.id = id; self.parent = parent; self.position = position; self.content = content; self.properties = properties
    }
    enum CodingKeys: String, CodingKey { case id, parent = "parent_id", position, content, properties }
}

public struct GammaDocument: Identifiable, Sendable {
    public var id: String
    public var title: String
    public var properties: [String: JSONValue]
    public var blocks: [GammaBlock]
    public init(root: GammaBlock, blocks: [GammaBlock]) {
        id = root.id; title = root.content; properties = root.properties; self.blocks = blocks
    }
}

public struct GammaOperation: Codable, Equatable, Sendable {
    public var op: String
    public var id: String
    public var parent: String?
    public var position: String?
    public var content: String?
    public var base: String?
    public var props: [String: JSONValue]?
    public var baseProps: [String: JSONValue]?
    public init(op: String, id: String, parent: String? = nil, position: String? = nil, content: String? = nil, base: String? = nil, props: [String: JSONValue]? = nil, baseProps: [String: JSONValue]? = nil) {
        self.op = op; self.id = id; self.parent = parent; self.position = position
        self.content = content; self.base = base; self.props = props; self.baseProps = baseProps
    }
    enum CodingKeys: String, CodingKey { case op, id, parent, position, content, base, props; case baseProps = "base_props" }
}

public enum GammaID {
    public static func make() -> String { UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased() }
}

public struct GammaMirror: Codable, Sendable {
    public var origin: String
    public var account: String
    public var workspaceID: String
    public var mode: String
    public var lastSync: String?
    public var lastError: String?
    public var running: Bool = false
    public var pending: Bool = false
    public var remoteCursor: String = ""
    public var retryPages: [String] = []
    public var lastMode: String = "two-way"
    public init(origin: String, account: String, workspaceID: String, mode: String = "two-way") {
        self.origin = origin; self.account = account; self.workspaceID = workspaceID; self.mode = mode
    }
}

public struct GammaConflict: Codable, Identifiable, Sendable {
    public var id: String
    public var pageID: String
    public var blockID: String
    public var kind: String
    public var base: String
    public var mine: String
    public var theirs: String
    public var result: String
    public init(pageID: String, blockID: String, kind: String, base: String = "", mine: String = "", theirs: String = "", result: String = "") {
        self.id = GammaID.make(); self.pageID = pageID; self.blockID = blockID; self.kind = kind
        self.base = base; self.mine = mine; self.theirs = theirs; self.result = result
    }
}

public enum GammaError: Error, LocalizedError {
    case invalid(String), missing(String), database(String), busy, authentication(String)
    case propertyChanged(String)
    public var errorDescription: String? {
        switch self {
        case .invalid(let s), .missing(let s), .database(let s), .authentication(let s): return s
        case .busy: return "A sync is already running."
        case .propertyChanged(let s): return "The handwriting changed on another device: \(s)"
        }
    }
}

public typealias GammaSnapshot = [String: GammaBlock]

enum GammaJSON {
    static func data<T: Encodable>(_ value: T) throws -> Data {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return try encoder.encode(value)
    }
    static func string<T: Encodable>(_ value: T) throws -> String { String(decoding: try data(value), as: UTF8.self) }
    static func decode<T: Decodable>(_ type: T.Type, _ string: String) throws -> T { try JSONDecoder().decode(type, from: Data(string.utf8)) }
    static func now() -> String { ISO8601DateFormatter().string(from: Date()) }
}
