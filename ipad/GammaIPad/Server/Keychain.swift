import Foundation
import Security

/// A few strings kept in the Keychain (generic passwords under this app's
/// service). The local server's seeded password lives here (LocalServer),
/// not in UserDefaults, which is a plain file in the app's container.
enum Keychain {
    private static let service = "net.gammapdf.ipad"

    static func string(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
              let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    /// False when the Keychain refused it. Readable after the first unlock
    /// (the server can start from a background launch) and kept in an
    /// encrypted backup, so a restored iPad gets the password together with
    /// the data directory it opens.
    @discardableResult
    static func set(_ value: String, account: String) -> Bool {
        let match: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(match as CFDictionary)
        var item = match
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        item[kSecValueData as String] = Data(value.utf8)
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }
}
