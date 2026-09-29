import UIKit
import GammaCore

@MainActor
final class NotesController: UITableViewController {
    let repository: GammaRepository
    let documentID: String
    private var rows: [(GammaBlock, Int)] = []
    init(repository: GammaRepository, documentID: String) {
        self.repository = repository; self.documentID = documentID; super.init(style: .insetGrouped)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func viewDidLoad() {
        super.viewDidLoad(); title = "Notes"
        navigationItem.leftBarButtonItem = UIBarButtonItem(barButtonSystemItem: .done, target: self, action: #selector(done))
        navigationItem.rightBarButtonItem = UIBarButtonItem(barButtonSystemItem: .add, target: self, action: #selector(addNote))
        Task { await reload() }
    }
    private func reload() async {
        do {
            let document = try await repository.document(id: documentID)
            rows = []
            var visited = Set<String>()
            func walk(_ parent: String, depth: Int) {
                guard depth < 100, visited.insert(parent).inserted else { return }
                for block in document.blocks.filter({ $0.parent == parent }).sorted(by: { $0.position < $1.position }) {
                    if propertyObject(block.properties)["type"] as? String != "notebook-sheet" { rows.append((block, depth)) }
                    walk(block.id, depth: depth + 1)
                }
            }
            walk(documentID, depth: 0); tableView.reloadData()
        } catch { show(error) }
    }
    override func tableView(_ tableView: UITableView, numberOfRowsInSection section: Int) -> Int { rows.count }
    override func tableView(_ tableView: UITableView, cellForRowAt indexPath: IndexPath) -> UITableViewCell {
        let cell = UITableViewCell(style: .subtitle, reuseIdentifier: nil), (block, depth) = rows[indexPath.row]
        cell.indentationLevel = depth; cell.indentationWidth = 16
        let props = propertyObject(block.properties)
        cell.textLabel?.text = block.content.isEmpty ? (props["ink_url"] == nil ? "Untitled note" : "Handwriting") : block.content
        cell.textLabel?.numberOfLines = 3
        if props["ink_url"] != nil { cell.imageView?.image = UIImage(systemName: "pencil.tip") }
        else if props["type"] as? String == "audio" { cell.imageView?.image = UIImage(systemName: "waveform") }
        else { cell.imageView?.image = UIImage(systemName: "text.alignleft") }
        cell.accessoryType = .disclosureIndicator; return cell
    }
    override func tableView(_ tableView: UITableView, didSelectRowAt indexPath: IndexPath) { edit(rows[indexPath.row].0) }
    override func tableView(_ tableView: UITableView, contextMenuConfigurationForRowAt indexPath: IndexPath, point: CGPoint) -> UIContextMenuConfiguration? {
        let block = rows[indexPath.row].0
        return UIContextMenuConfiguration(identifier: nil, previewProvider: nil) { [weak self] _ in
            UIMenu(children: [UIAction(title: "Add child note") { _ in self?.edit(nil, parent: block.id) }])
        }
    }
    @objc private func done() { dismiss(animated: true) }
    @objc private func addNote() { edit(nil) }
    private func edit(_ block: GammaBlock?, parent: String? = nil) {
        let editor = TextNoteController(text: block?.content ?? "") { [weak self] text in
            guard let self else { return }
            Task {
                do {
                    let op: GammaOperation
                    if let block { op = GammaOperation(op: "set", id: block.id, content: text, base: block.content) }
                    else { op = GammaOperation(op: "insert", id: gammaID(), parent: parent ?? self.documentID, content: text) }
                    try await self.repository.apply(pageID: self.documentID, ops: [op]); await self.reload()
                    self.navigationController?.popViewController(animated: true)
                } catch { self.show(error) }
            }
        }
        navigationController?.pushViewController(editor, animated: true)
    }
    private func show(_ error: Error) {
        let alert = UIAlertController(title: "Could not save", message: error.localizedDescription, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "OK", style: .default)); (navigationController?.topViewController ?? self).present(alert, animated: true)
    }
}

@MainActor
final class TextNoteController: UIViewController {
    let textView = UITextView()
    let save: (String) -> Void
    init(text: String, save: @escaping (String) -> Void) { self.save = save; super.init(nibName: nil, bundle: nil); textView.text = text }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func viewDidLoad() {
        super.viewDidLoad(); title = "Edit note"; view.backgroundColor = .systemBackground
        textView.font = .preferredFont(forTextStyle: .body); textView.textContainerInset = UIEdgeInsets(top: 20, left: 16, bottom: 20, right: 16)
        textView.translatesAutoresizingMaskIntoConstraints = false; view.addSubview(textView)
        NSLayoutConstraint.activate([textView.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor), textView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            textView.trailingAnchor.constraint(equalTo: view.trailingAnchor), textView.bottomAnchor.constraint(equalTo: view.keyboardLayoutGuide.topAnchor)])
        navigationItem.rightBarButtonItem = UIBarButtonItem(title: "Save", style: .done, target: self, action: #selector(saveNote))
        textView.becomeFirstResponder()
    }
    @objc private func saveNote() { save(textView.text) }
}
