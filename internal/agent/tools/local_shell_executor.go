package tools

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"time"

	"github.com/Tencent/WeKnora/internal/sandbox"
)

// LocalShellExecutor executes commands on the current system or container.
// It satisfies SandboxCommandExecutor so it can be passed directly to NewShellExecTool.
type LocalShellExecutor struct {
	defaultWorkDir string
}

// NewLocalShellExecutor creates a local shell executor.
func NewLocalShellExecutor(defaultWorkDir string) *LocalShellExecutor {
	if defaultWorkDir == "" {
		defaultWorkDir = "/tmp/weknora_workspace"
	}
	return &LocalShellExecutor{defaultWorkDir: defaultWorkDir}
}

// ExecShellCommand executes a command using /bin/bash or /bin/sh.
func (e *LocalShellExecutor) ExecShellCommand(
	ctx context.Context,
	sessionID string,
	command string,
	workDir string,
	timeout time.Duration,
	env map[string]string,
) (*sandbox.ExecuteResult, error) {
	if workDir == "" {
		workDir = e.defaultWorkDir
	}
	_ = os.MkdirAll(workDir, 0755)

	if timeout <= 0 {
		timeout = defaultShellExecTimeout
	}
	if timeout > shellExecMaxTimeout {
		timeout = shellExecMaxTimeout
	}

	cmdCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	// Try /bin/bash first, fall back to /bin/sh
	shellPath := "/bin/bash"
	if _, err := os.Stat(shellPath); err != nil {
		shellPath = "/bin/sh"
	}

	cmd := exec.CommandContext(cmdCtx, shellPath, "-c", command)
	cmd.Dir = workDir

	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, fmt.Sprintf("%s=%s", k, v))
	}

	var stdoutBuf, stderrBuf bytes.Buffer
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf

	start := time.Now()
	err := cmd.Run()
	duration := time.Since(start)

	exitCode := 0
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
		} else if cmdCtx.Err() == context.DeadlineExceeded {
			return &sandbox.ExecuteResult{
				ExitCode: -1,
				Stderr:   "command timed out",
				Duration: duration,
			}, nil
		} else {
			return nil, err
		}
	}

	return &sandbox.ExecuteResult{
		ExitCode: exitCode,
		Stdout:   stdoutBuf.String(),
		Stderr:   stderrBuf.String(),
		Duration: duration,
	}, nil
}
