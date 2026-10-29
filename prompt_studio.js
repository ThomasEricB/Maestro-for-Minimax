// No kernel.port() here on purpose. Pinokio allocates from 42000 upward and recycles those ports
// between apps, so the studio kept landing on a port Maestro had previously held -- and the browser
// replayed Maestro's cached page against it, which reads as "the studio just opens Maestro". The studio
// picks its own port well clear of that band and reports it on the line captured below.
module.exports = async (kernel) => {
  return {
    // Stays up after the run array finishes, like start.js: it is a server, and killing the shell would
    // take the page down with it.
    daemon: true,
    run: [
      {
        method: "shell.run",
        params: {
          venv: "env",
          // The launcher lives in the project root, so the shell starts there; "app" puts us next to the
          // venv, and the studio itself is one level up.
          path: "app",
          message: [
            // Maestro's own port is assigned at launch, so pinokio.js passes the running instance's URL
            // through params rather than letting the studio guess. "auto" means no URL was passed and
            // the studio should find Maestro itself; the explicit !== 'null' check is because an absent
            // param arrives here as the string "null", which is truthy.
            `python ../h3_prompt_studio.py --no-browser --maestro {{args.maestro && args.maestro !== 'null' ? args.maestro : 'auto'}}`
          ],
          on: [{
            // Matched against the marker the studio prints, not a bare URL: the command line above also
            // carries an http:// address (--maestro), and the shell echoes it first, so a generic pattern
            // captures Maestro's URL and the menu entry then opens Maestro instead of the studio.
            "event": "/PROMPT_STUDIO_URL (http:\/\/[0-9.:]+)/",
            "done": true
          }]
        }
      },
      {
        method: "local.set",
        params: {
          url: "{{input.event[1]}}"
        }
      }
    ]
  }
}
