# Source explorer and citations

The project **Explore** route loads the file listing from `GET /api/v1/repositories/{repository_id}/files`. It builds a folder tree from repository-relative paths and filters it locally as the user searches. Selecting a file loads its complete current content from `GET /api/v1/repositories/{repository_id}/files/content?path=...`.

The viewer is read only. It renders line numbers and lightweight Java token colors without Monaco or source execution. It makes no write request. All requests use the active repository ID and the backend's approved checkout boundary.

Each chat answer retains its own citation list. Clicking a citation opens a resizable side panel over the same chat, selects its file, scrolls to the first cited line, and highlights the inclusive line range. Previous and Next move through citations from that answer. Closing the panel leaves the chat turns and agent activity mounted. Keyboard users can resize the panel with Left and Right arrows on the resize handle; Escape closes it.

The file API reads the current checkout, while citations describe evidence from an index snapshot. If the file is missing from the listing or the cited range is outside the current file, the panel explains that the citation is stale and recommends reindexing. An API error is shown with a retry action. The viewer never fabricates lines or silently substitutes another file.

Dependency relationship links use the same explorer route with `path`, `start_line`, and `end_line` query parameters. The route validates a relative path and positive inclusive line range before passing them to the read-only browser.
