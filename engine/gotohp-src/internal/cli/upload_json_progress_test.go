package cli

import (
	"bufio"
	"bytes"
	"encoding/json"
	"strings"
	"sync"
	"testing"

	"app/backend"
)

func TestJSONProgressReporterEmitsSanitizedByteProgress(t *testing.T) {
	var output bytes.Buffer
	reporter := teaReporter{progressOut: &output, progressMu: &sync.Mutex{}}
	reporter.ThreadStatus(backend.ThreadStatus{
		WorkerID: 1, Status: "uploading",
		FilePath: "/Users/private/Photos/secret-folder/GX014355.MP4",
		FileName: "GX014355.MP4", Message: "Uploading...",
		BytesUploaded: 1024, BytesTotal: 4096, Attempt: 2,
	})

	scanner := bufio.NewScanner(&output)
	if !scanner.Scan() {
		t.Fatal("expected one JSONL progress event")
	}
	var event map[string]any
	if err := json.Unmarshal(scanner.Bytes(), &event); err != nil {
		t.Fatalf("invalid progress JSON: %v", err)
	}
	if event["event"] != "thread_status" || event["status"] != "uploading" {
		t.Fatalf("unexpected event: %#v", event)
	}
	if event["file_name"] != "GX014355.MP4" ||
		event["bytes_uploaded"] != float64(1024) ||
		event["bytes_total"] != float64(4096) ||
		event["attempt"] != float64(2) {
		t.Fatalf("missing byte progress fields: %#v", event)
	}
	encoded := output.String()
	for _, private := range []string{"/Users/private", "secret-folder", "FilePath", "Message"} {
		if strings.Contains(encoded, private) {
			t.Fatalf("progress event leaked private field %q: %s", private, encoded)
		}
	}
}

func TestJSONProgressReporterWritesOneLinePerEvent(t *testing.T) {
	var output bytes.Buffer
	reporter := teaReporter{progressOut: &output, progressMu: &sync.Mutex{}}
	reporter.UploadStart(backend.UploadBatchStart{Total: 3, TotalBytes: 9000})
	reporter.UploadStop()
	lines := strings.Split(strings.TrimSpace(output.String()), "\n")
	if len(lines) != 2 {
		t.Fatalf("expected 2 JSONL events, got %d: %q", len(lines), output.String())
	}
	var start map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &start); err != nil {
		t.Fatal(err)
	}
	if start["event"] != "upload_start" || start["total_files"] != float64(3) ||
		start["total_bytes"] != float64(9000) {
		t.Fatalf("unexpected upload start: %#v", start)
	}
	var stop map[string]any
	if err := json.Unmarshal([]byte(lines[1]), &stop); err != nil {
		t.Fatal(err)
	}
	if stop["event"] != "upload_stop" {
		t.Fatalf("unexpected upload stop: %#v", stop)
	}
}
