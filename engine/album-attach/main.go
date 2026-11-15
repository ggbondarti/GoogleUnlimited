// album-attach is an optional, bounded local repair utility.
// It only adds already-uploaded media keys to one existing Photos album.
// It never uploads files, creates albums, or deletes remote media.
package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"regexp"

	"app/backend"
)

type request struct {
	MediaKeys []string `json:"media_keys"`
}

func main() {
	config := flag.String("config", "", "path to local gotohp credential file")
	album := flag.String("album-key", "", "existing Google Photos album ID")
	keysFile := flag.String("keys-file", "", "private JSON with media_keys array")
	execute := flag.Bool("execute", false, "perform API additions (default dry run)")
	flag.Parse()
	if err := run(*config, *album, *keysFile, *execute); err != nil {
		fmt.Fprintln(os.Stderr, "album-attach failed:", err)
		os.Exit(1)
	}
}

func run(config, album, keysFile string, execute bool) error {
	if !regexp.MustCompile(`^AF1Qip[A-Za-z0-9_-]{8,500}$`).MatchString(album) {
		return errors.New("existing album key required")
	}
	if keysFile == "" {
		return errors.New("keys-file required")
	}
	b, err := os.ReadFile(keysFile)
	if err != nil {
		return err
	}
	var payload request
	if err := json.Unmarshal(b, &payload); err != nil {
		return err
	}
	if len(payload.MediaKeys) == 0 || len(payload.MediaKeys) > 500 {
		return errors.New("batch size must be 1..500")
	}
	seen := make(map[string]bool)
	for _, k := range payload.MediaKeys {
		if len(k) < 8 || len(k) > 500 || seen[k] {
			return errors.New("invalid or duplicate media key")
		}
		seen[k] = true
	}
	fmt.Printf("validated=%d execute=%t\n", len(payload.MediaKeys), execute)
	if !execute {
		return nil
	}
	if config == "" {
		return errors.New("config required in execute mode")
	}
	if err := backend.LoadConfig(config); err != nil {
		return errors.New("local account config could not load")
	}
	api, err := backend.NewApi(backend.ApiOptions{})
	if err != nil {
		return errors.New("local account could not initialize")
	}
	if err := api.AddMediaToAlbum(album, payload.MediaKeys); err != nil {
		return errors.New("remote album attachment failed; check local account and album membership")
	}
	fmt.Printf("album_membership_accepted=%d\n", len(payload.MediaKeys))
	return nil
}
