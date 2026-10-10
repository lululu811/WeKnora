package tools

import (
	"context"
	"testing"
	"time"
)

func TestLocalShellExecutor(t *testing.T) {
	executor := NewLocalShellExecutor(t.TempDir())
	ctx := context.Background()

	// Simple echo command
	res, err := executor.ExecShellCommand(ctx, "session-1", "echo 'hello world'", "", 5*time.Second, nil)
	if err != nil {
		t.Fatalf("ExecShellCommand failed: %v", err)
	}
	if res.ExitCode != 0 {
		t.Fatalf("ExitCode = %d, want 0", res.ExitCode)
	}
	if res.Stdout != "hello world\n" {
		t.Fatalf("Stdout = %q, want 'hello world\\n'", res.Stdout)
	}

	// Environment variable injection
	env := map[string]string{"MY_TEST_VAR": "foo_bar"}
	res, err = executor.ExecShellCommand(ctx, "session-1", "echo $MY_TEST_VAR", "", 5*time.Second, env)
	if err != nil {
		t.Fatalf("ExecShellCommand with env failed: %v", err)
	}
	if res.Stdout != "foo_bar\n" {
		t.Fatalf("Stdout = %q, want 'foo_bar\\n'", res.Stdout)
	}

	// Non-zero exit code
	res, err = executor.ExecShellCommand(ctx, "session-1", "exit 42", "", 5*time.Second, nil)
	if err != nil {
		t.Fatalf("ExecShellCommand with non-zero exit failed: %v", err)
	}
	if res.ExitCode != 42 {
		t.Fatalf("ExitCode = %d, want 42", res.ExitCode)
	}
}
