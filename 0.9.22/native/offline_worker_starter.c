/*
 * Windowless starter for the exact World of Tanks 0.9.22 #1513 clients.
 *
 * #1513 hard-codes ShowWindow(SW_SHOW) for its main HWND, so STARTUPINFO's
 * wShowWindow cannot suppress the first frame.  Starting it on a private,
 * never-switched desktop keeps that HWND and every child window off the
 * player's desktop without patching or copying the client.
 */

#define WIN32_LEAN_AND_MEAN
#define _WIN32_WINNT 0x0600
#include <winsock2.h>
#include <windows.h>
#include <tlhelp32.h>
#include <strsafe.h>


#define WORKER_MUTEX_NAME L"Local\\offline_lan_0922_worker"
#define WORKER_MODE_ENV L"OFFLINE_LAN_0922_CLIENT_MODE"
#define WORKER_MODE_VALUE L"simulation_worker"
#define MULTI_CLIENT_ENV L"OFFLINE_LAN_0922_ALLOW_MULTIPLE_CLIENTS"
#define MULTI_CLIENT_VALUE L"1"
#define HIDDEN_DESKTOP_ENV L"OFFLINE_LAN_0922_HIDDEN_DESKTOP"
#define HIDDEN_DESKTOP_VALUE L"1"
#define WORKER_READY_MARKER_ENV L"OFFLINE_LAN_0922_WORKER_READY_MARKER"
#define WORKER_READY_MARKER_FILE L"offline-worker.ready"
#define SERVER_FILENAME L"WoT-0.9.22-LAN-Server.exe"
#define SERVER_HOST_ENV L"OFFLINE_LAN_0922_SERVER_HOST"
#define SERVER_HOST_VALUE L"127.0.0.1"
#define SERVER_PORT_ENV L"OFFLINE_LAN_0922_SERVER_PORT"
#define SERVER_PORT_VALUE L"28782"
#define SERVER_LOOPBACK_ENV L"WOT_0922_LOOPBACK_ONLY"
#define SERVER_LOOPBACK_VALUE L"1"
#define SERVER_DATA_ENV L"WOT_0922_SERVER_DATA"
#define SERVER_DATA_RELATIVE L"mods\\configs\\offline_lan_0922"
#define SERVER_PORT 28782
#define PLAYER_MODE L"--player"
#define PAIRED_PLAYER_MODE L"--paired-player"
#define WORKER_ONLY_MODE L"--worker-only"
#define SERVER_READY_TIMEOUT_MS 30000
#define WORKER_READY_TIMEOUT_MS 60000
#define WORKER_READY_POLL_MS 50
#define PLAYER_HANDOFF_GRACE_MS 10000
#define PLAYER_HANDOFF_POLL_MS 100
#define MAX_GAME_PROCESS_IDS 32


static WCHAR g_root[MAX_PATH];
static WCHAR g_ready_marker[MAX_PATH];


typedef struct GameProcessSet {
	DWORD count;
	DWORD ids[MAX_GAME_PROCESS_IDS];
} GameProcessSet;


static void log_failure(const char *stage, DWORD error_code)
{
	WCHAR log_path[MAX_PATH];
	char message[256];
	DWORD written = 0;
	HANDLE file;
	if (FAILED(StringCchCopyW(log_path, MAX_PATH, g_root)) ||
			FAILED(StringCchCatW(log_path, MAX_PATH,
				L"offline-worker-starter.log"))) {
		return;
	}
	file = CreateFileW(log_path, GENERIC_WRITE, FILE_SHARE_READ, 0,
		CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, 0);
	if (file == INVALID_HANDLE_VALUE) {
		return;
	}
	if (FAILED(StringCchPrintfA(message, 256,
			"stage=%s win32_error=%lu\r\n", stage,
			(unsigned long)error_code))) {
		CloseHandle(file);
		return;
	}
	WriteFile(file, message, (DWORD)lstrlenA(message), &written, 0);
	CloseHandle(file);
}


static void clear_failure_log(void)
{
	WCHAR log_path[MAX_PATH];
	if (SUCCEEDED(StringCchCopyW(log_path, MAX_PATH, g_root)) &&
			SUCCEEDED(StringCchCatW(
				log_path, MAX_PATH, L"offline-worker-starter.log"))) {
		DeleteFileW(log_path);
	}
}


static int resolve_game_root(WCHAR *game_path, size_t game_path_count)
{
	WCHAR starter_path[MAX_PATH];
	DWORD length;
	int index;
	length = GetModuleFileNameW(0, starter_path, MAX_PATH);
	if (length == 0 || length >= MAX_PATH) {
		return 0;
	}
	for (index = (int)length - 1; index >= 0; --index) {
		if (starter_path[index] == L'\\' || starter_path[index] == L'/') {
			starter_path[index + 1] = L'\0';
			break;
		}
	}
	if (index < 0 || FAILED(StringCchCopyW(g_root, MAX_PATH,
			starter_path)) || FAILED(StringCchCopyW(game_path,
			game_path_count, starter_path)) ||
			FAILED(StringCchCatW(game_path, game_path_count,
				L"WorldOfTanks.exe"))) {
		return 0;
	}
	return GetFileAttributesW(game_path) != INVALID_FILE_ATTRIBUTES;
}


static int configure_kill_job(HANDLE job)
{
	JOBOBJECT_EXTENDED_LIMIT_INFORMATION info;
	ZeroMemory(&info, sizeof(info));
	info.BasicLimitInformation.LimitFlags =
		JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
	return SetInformationJobObject(job, JobObjectExtendedLimitInformation,
		&info, sizeof(info)) != FALSE;
}


static int configure_ready_marker(void)
{
	if (FAILED(StringCchCopyW(g_ready_marker, MAX_PATH, g_root)) ||
			FAILED(StringCchCatW(g_ready_marker, MAX_PATH,
				WORKER_READY_MARKER_FILE))) {
		return 0;
	}
	return 1;
}


static int sibling_path(WCHAR *path, size_t path_count,
		const WCHAR *filename)
{
	return SUCCEEDED(StringCchCopyW(path, path_count, g_root)) &&
		SUCCEEDED(StringCchCatW(path, path_count, filename));
}


static int local_server_is_listening(void)
{
	SOCKET connection;
	struct sockaddr_in address;
	int connected;
	connection = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
	if (connection == INVALID_SOCKET) {
		return 0;
	}
	ZeroMemory(&address, sizeof(address));
	address.sin_family = AF_INET;
	address.sin_port = htons(SERVER_PORT);
	address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
	connected = connect(connection, (struct sockaddr *)&address,
		sizeof(address)) == 0;
	closesocket(connection);
	return connected;
}


static int wait_for_local_server(HANDLE server_process)
{
	DWORD elapsed = 0;
	DWORD server_state;
	DWORD exit_code;
	while (elapsed <= SERVER_READY_TIMEOUT_MS) {
		server_state = WaitForSingleObject(server_process, 0);
		if (server_state != WAIT_TIMEOUT) {
			exit_code = ERROR_PROCESS_ABORTED;
			if (server_state == WAIT_FAILED) {
				exit_code = GetLastError();
			} else if (!GetExitCodeProcess(server_process, &exit_code)) {
				exit_code = GetLastError();
			}
			log_failure("local_server_exited", exit_code);
			return 0;
		}
		if (local_server_is_listening()) {
			return 1;
		}
		if (elapsed == SERVER_READY_TIMEOUT_MS) {
			break;
		}
		Sleep(WORKER_READY_POLL_MS);
		elapsed += WORKER_READY_POLL_MS;
	}
	log_failure("wait_for_local_server", WAIT_TIMEOUT);
	return 0;
}


static int remove_ready_marker(void)
{
	WCHAR temporary_path[MAX_PATH];
	DWORD error_code;
	if (!DeleteFileW(g_ready_marker)) {
		error_code = GetLastError();
		if (error_code != ERROR_FILE_NOT_FOUND &&
				error_code != ERROR_PATH_NOT_FOUND) {
			SetLastError(error_code);
			return 0;
		}
	}
	if (FAILED(StringCchCopyW(temporary_path, MAX_PATH,
			g_ready_marker)) ||
			FAILED(StringCchCatW(temporary_path, MAX_PATH, L".tmp"))) {
		SetLastError(ERROR_INSUFFICIENT_BUFFER);
		return 0;
	}
	if (!DeleteFileW(temporary_path)) {
		error_code = GetLastError();
		if (error_code != ERROR_FILE_NOT_FOUND &&
				error_code != ERROR_PATH_NOT_FOUND) {
			SetLastError(error_code);
			return 0;
		}
	}
	return 1;
}


static int wait_for_worker_ready(HANDLE worker_process, HANDLE server_process)
{
	DWORD elapsed = 0;
	DWORD marker_attributes;
	DWORD worker_state;
	while (elapsed <= WORKER_READY_TIMEOUT_MS) {
		if (server_process != 0 &&
				WaitForSingleObject(server_process, 0) != WAIT_TIMEOUT) {
			log_failure("local_server_exited_before_worker_ready",
				ERROR_PROCESS_ABORTED);
			return 0;
		}
		worker_state = WaitForSingleObject(worker_process, 0);
		if (worker_state != WAIT_TIMEOUT) {
			log_failure("worker_exited_before_ready",
				worker_state == WAIT_FAILED ? GetLastError() :
				ERROR_PROCESS_ABORTED);
			return 0;
		}
		marker_attributes = GetFileAttributesW(g_ready_marker);
		if (marker_attributes != INVALID_FILE_ATTRIBUTES &&
				!(marker_attributes & FILE_ATTRIBUTE_DIRECTORY)) {
			/* Reject a marker published immediately before worker death. */
			worker_state = WaitForSingleObject(worker_process, 0);
			if (worker_state == WAIT_TIMEOUT) {
				return 1;
			}
			log_failure("worker_exited_after_ready",
				worker_state == WAIT_FAILED ? GetLastError() :
				ERROR_PROCESS_ABORTED);
			return 0;
		}
		if (elapsed == WORKER_READY_TIMEOUT_MS) {
			break;
		}
		Sleep(WORKER_READY_POLL_MS);
		elapsed += WORKER_READY_POLL_MS;
	}
	log_failure("wait_for_worker_ready", WAIT_TIMEOUT);
	return 0;
}


static int collect_game_processes(const WCHAR *game_path,
		GameProcessSet *processes)
{
	PROCESSENTRY32W entry;
	HANDLE process;
	HANDLE snapshot;
	WCHAR process_path[MAX_PATH];
	DWORD process_path_count;
	processes->count = 0;
	snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
	if (snapshot == INVALID_HANDLE_VALUE) {
		return 0;
	}
	ZeroMemory(&entry, sizeof(entry));
	entry.dwSize = sizeof(entry);
	if (!Process32FirstW(snapshot, &entry)) {
		DWORD error_code = GetLastError();
		CloseHandle(snapshot);
		SetLastError(error_code);
		return 0;
	}
	do {
		if (lstrcmpiW(entry.szExeFile, L"WorldOfTanks.exe") != 0) {
			continue;
		}
		process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE,
			entry.th32ProcessID);
		if (process == 0) {
			continue;
		}
		process_path_count = MAX_PATH;
		if (QueryFullProcessImageNameW(process, 0, process_path,
				&process_path_count) &&
				lstrcmpiW(process_path, game_path) == 0) {
			if (processes->count >= MAX_GAME_PROCESS_IDS) {
				CloseHandle(process);
				CloseHandle(snapshot);
				SetLastError(ERROR_INSUFFICIENT_BUFFER);
				return 0;
			}
			processes->ids[processes->count++] = entry.th32ProcessID;
		}
		CloseHandle(process);
	} while (Process32NextW(snapshot, &entry));
	CloseHandle(snapshot);
	return 1;
}


static int process_set_contains(const GameProcessSet *processes, DWORD id)
{
	DWORD index;
	for (index = 0; index < processes->count; ++index) {
		if (processes->ids[index] == id) {
			return 1;
		}
	}
	return 0;
}


static int has_new_game_process(const GameProcessSet *current,
		const GameProcessSet *baseline)
{
	DWORD index;
	for (index = 0; index < current->count; ++index) {
		if (!process_set_contains(baseline, current->ids[index])) {
			return 1;
		}
	}
	return 0;
}


static int wait_for_paired_player_handoff(const WCHAR *game_path,
		const GameProcessSet *baseline_processes)
{
	GameProcessSet current_processes;
	DWORD quiet_ms = 0;
	while (quiet_ms < PLAYER_HANDOFF_GRACE_MS) {
		if (!collect_game_processes(game_path, &current_processes)) {
			log_failure("collect_game_processes(player)", GetLastError());
			return 0;
		}
		/* Baseline PIDs belong to clients that predate this launch, normally
		 * the hidden simulation worker. A visible client handed off through an
		 * external broker has a new PID even if the worker exits simultaneously. */
		if (has_new_game_process(&current_processes, baseline_processes)) {
			quiet_ms = 0;
		} else {
			quiet_ms += PLAYER_HANDOFF_POLL_MS;
		}
		Sleep(PLAYER_HANDOFF_POLL_MS);
	}
	return 1;
}


static int launch_player(const WCHAR *game_path, BOOL paired_worker)
{
	WCHAR child_command[2 * MAX_PATH];
	STARTUPINFOW startup;
	PROCESS_INFORMATION process;
	JOBOBJECT_BASIC_ACCOUNTING_INFORMATION accounting;
	HANDLE player_job = 0;
	DWORD exit_code = 1;
	DWORD wait_state;
	GameProcessSet baseline_game_processes;
	BOOL baseline_collected = FALSE;
	int result = 1;
	if (paired_worker) {
		if (!SetEnvironmentVariableW(MULTI_CLIENT_ENV, MULTI_CLIENT_VALUE)) {
			log_failure("SetEnvironmentVariableW", GetLastError());
			return 20;
		}
	} else {
		SetEnvironmentVariableW(MULTI_CLIENT_ENV, 0);
		SetEnvironmentVariableW(SERVER_HOST_ENV, 0);
		SetEnvironmentVariableW(SERVER_PORT_ENV, 0);
	}
	/* Deleting an absent variable is harmless even though Win32 reports it. */
	SetEnvironmentVariableW(WORKER_MODE_ENV, 0);
	SetEnvironmentVariableW(HIDDEN_DESKTOP_ENV, 0);
	SetEnvironmentVariableW(WORKER_READY_MARKER_ENV, 0);
	if (FAILED(StringCchPrintfW(child_command, 2 * MAX_PATH,
			L"\"%s\" --config engine_config.offline-player.xml "
			L"--logFilePrefix offline-player-", game_path))) {
		log_failure("player_command", ERROR_INSUFFICIENT_BUFFER);
		return 21;
	}
	ZeroMemory(&startup, sizeof(startup));
	startup.cb = sizeof(startup);
	ZeroMemory(&process, sizeof(process));
	ZeroMemory(&baseline_game_processes, sizeof(baseline_game_processes));
	if (paired_worker) {
		baseline_collected = collect_game_processes(
			game_path, &baseline_game_processes);
	}
	player_job = CreateJobObjectW(0, 0);
	if (player_job == 0 || !configure_kill_job(player_job)) {
		log_failure("CreateJobObjectW(player)", GetLastError());
		if (player_job != 0) {
			CloseHandle(player_job);
		}
		return 22;
	}
	if (!CreateProcessW(game_path, child_command, 0, 0, FALSE,
			CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP, 0,
			g_root, &startup, &process)) {
		log_failure("CreateProcessW(player)", GetLastError());
		CloseHandle(player_job);
		return 22;
	}
	if (!AssignProcessToJobObject(player_job, process.hProcess)) {
		log_failure("AssignProcessToJobObject(player)", GetLastError());
		TerminateProcess(process.hProcess, 22);
		result = 22;
		goto player_cleanup;
	}
	if (ResumeThread(process.hThread) == (DWORD)-1) {
		log_failure("ResumeThread(player)", GetLastError());
		TerminateJobObject(player_job, 22);
		result = 22;
		goto player_cleanup;
	}
	CloseHandle(process.hThread);
	process.hThread = 0;
	/* The stock client can hand off to another WorldOfTanks.exe process. Every
	 * descendant remains in this private job, so the launcher-visible starter
	 * exits only after the whole visible client tree is gone. */
	for (;;) {
		ZeroMemory(&accounting, sizeof(accounting));
		if (!QueryInformationJobObject(
				player_job, JobObjectBasicAccountingInformation,
				&accounting, sizeof(accounting), 0)) {
			log_failure("QueryInformationJobObject(player)", GetLastError());
			TerminateJobObject(player_job, 23);
			result = 23;
			goto player_cleanup;
		}
		if (accounting.ActiveProcesses == 0) {
			if (process.hProcess != 0 &&
					!GetExitCodeProcess(process.hProcess, &exit_code)) {
				exit_code = 24;
				log_failure("GetExitCodeProcess(player)", GetLastError());
			}
			if (paired_worker && baseline_collected &&
					!wait_for_paired_player_handoff(
						game_path, &baseline_game_processes)) {
				result = 25;
				goto player_cleanup;
			}
			break;
		}
		if (process.hProcess == 0) {
			Sleep(100);
			continue;
		}
		wait_state = WaitForSingleObject(process.hProcess, 100);
		if (wait_state == WAIT_FAILED) {
			log_failure("WaitForSingleObject(player)", GetLastError());
			TerminateJobObject(player_job, 23);
			result = 23;
			goto player_cleanup;
		}
		if (wait_state == WAIT_OBJECT_0) {
			if (!GetExitCodeProcess(process.hProcess, &exit_code)) {
				exit_code = 24;
				log_failure("GetExitCodeProcess(player)", GetLastError());
			}
			/* The original handle is no longer needed after its exit code is
			 * saved. Release it while the job keeps tracking replacement
			 * descendants. */
			CloseHandle(process.hProcess);
			process.hProcess = 0;
		}
	}
	result = (int)exit_code;

player_cleanup:
	if (process.hThread != 0) {
		CloseHandle(process.hThread);
	}
	if (process.hProcess != 0) {
		CloseHandle(process.hProcess);
	}
	CloseHandle(player_job);
	return result;
}


int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous_instance,
		LPWSTR command_line, int show_command)
{
	WCHAR game_path[MAX_PATH];
	WCHAR server_path[MAX_PATH];
	WCHAR server_data_path[MAX_PATH];
	WCHAR child_command[2 * MAX_PATH];
	WCHAR desktop_name[96];
	WCHAR full_desktop_name[128];
	STARTUPINFOW startup;
	PROCESS_INFORMATION process;
	PROCESS_INFORMATION server_process;
	HANDLE singleton = 0;
	HANDLE desktop = 0;
	HANDLE job = 0;
	DWORD error_code = ERROR_SUCCESS;
	DWORD child_exit_code = 1;
	BOOL child_created = FALSE;
	BOOL server_created = FALSE;
	BOOL worker_only = FALSE;
	BOOL sockets_started = FALSE;
	WSADATA socket_data;
	int result = 1;
	(void)instance;
	(void)previous_instance;
	(void)show_command;

	ZeroMemory(&process, sizeof(process));
	ZeroMemory(&server_process, sizeof(server_process));
	g_root[0] = L'\0';
	g_ready_marker[0] = L'\0';
	if (!resolve_game_root(game_path, MAX_PATH)) {
		error_code = GetLastError();
		if (error_code == ERROR_SUCCESS) {
			error_code = ERROR_FILE_NOT_FOUND;
		}
		log_failure("resolve_game_root", error_code);
		return 4;
	}
	if (!configure_ready_marker()) {
		log_failure("ready_marker", ERROR_INSUFFICIENT_BUFFER);
		return 5;
	}
	if (lstrcmpiW(command_line, PLAYER_MODE) == 0) {
		return launch_player(game_path, FALSE);
	}
	if (lstrcmpiW(command_line, PAIRED_PLAYER_MODE) == 0) {
		return launch_player(game_path, TRUE);
	}
	worker_only = lstrcmpiW(command_line, WORKER_ONLY_MODE) == 0;

	singleton = CreateMutexW(0, TRUE, WORKER_MUTEX_NAME);
	if (singleton == 0) {
		log_failure("CreateMutexW", GetLastError());
		return 2;
	}
	if (GetLastError() == ERROR_ALREADY_EXISTS) {
		CloseHandle(singleton);
		return 3;
	}
	clear_failure_log();
	/* Only the mutex owner may replace the shared ready marker.  Otherwise a
	 * rapid second launch can erase the live worker's just-published marker. */
	if (!remove_ready_marker()) {
		log_failure("remove_ready_marker", GetLastError());
		result = 5;
		goto worker_cleanup;
	}

	job = CreateJobObjectW(0, 0);
	if (job == 0 || !configure_kill_job(job)) {
		error_code = GetLastError();
		log_failure("CreateJobObjectW", error_code);
		if (job != 0) {
			CloseHandle(job);
			job = 0;
		}
		result = 9;
		goto worker_cleanup;
	}

	if (!worker_only) {
		if (!sibling_path(
				server_data_path, MAX_PATH, SERVER_DATA_RELATIVE) ||
				!SetEnvironmentVariableW(
					SERVER_DATA_ENV, server_data_path) ||
				!SetEnvironmentVariableW(SERVER_HOST_ENV, SERVER_HOST_VALUE) ||
				!SetEnvironmentVariableW(SERVER_PORT_ENV,
					SERVER_PORT_VALUE)) {
			log_failure("SetEnvironmentVariableW(server endpoint)",
				GetLastError());
			result = 7;
			goto worker_cleanup;
		}
		if (WSAStartup(MAKEWORD(2, 2), &socket_data) != 0) {
			log_failure("WSAStartup", WSAGetLastError());
			result = 14;
			goto worker_cleanup;
		}
		sockets_started = TRUE;
		if (local_server_is_listening()) {
			log_failure("local_server_port_in_use", WSAEADDRINUSE);
			result = 15;
			goto worker_cleanup;
		}
		if (!sibling_path(server_path, MAX_PATH, SERVER_FILENAME) ||
					GetFileAttributesW(server_path) ==
					INVALID_FILE_ATTRIBUTES) {
			log_failure("local_server_missing", ERROR_FILE_NOT_FOUND);
			result = 15;
			goto worker_cleanup;
		}
		if (!SetEnvironmentVariableW(
					SERVER_LOOPBACK_ENV, SERVER_LOOPBACK_VALUE)) {
			log_failure("SetEnvironmentVariableW(loopback server)",
					GetLastError());
			result = 7;
			goto worker_cleanup;
		}
		ZeroMemory(&startup, sizeof(startup));
		startup.cb = sizeof(startup);
		server_created = CreateProcessW(
				server_path, 0, 0, 0, FALSE,
				CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP |
				CREATE_NO_WINDOW,
				0, g_root, &startup, &server_process);
		if (!server_created) {
				error_code = GetLastError();
				SetEnvironmentVariableW(SERVER_LOOPBACK_ENV, 0);
				log_failure("CreateProcessW(local server)", error_code);
				result = 16;
				goto worker_cleanup;
		}
		SetEnvironmentVariableW(SERVER_LOOPBACK_ENV, 0);
		if (!AssignProcessToJobObject(job, server_process.hProcess)) {
				error_code = GetLastError();
				log_failure("AssignProcessToJobObject(local server)",
					error_code);
				TerminateProcess(server_process.hProcess, 17);
				WaitForSingleObject(server_process.hProcess, INFINITE);
				result = 17;
				goto worker_cleanup;
		}
		if (ResumeThread(server_process.hThread) == (DWORD)-1) {
				error_code = GetLastError();
				log_failure("ResumeThread(local server)", error_code);
				TerminateProcess(server_process.hProcess, 18);
				WaitForSingleObject(server_process.hProcess, INFINITE);
				result = 18;
				goto worker_cleanup;
		}
		CloseHandle(server_process.hThread);
		server_process.hThread = 0;
		if (!wait_for_local_server(server_process.hProcess)) {
				result = 19;
				goto worker_cleanup;
		}
	}

	if (FAILED(StringCchPrintfW(desktop_name, 96,
			L"OfflineLanWorker_%lu",
			(unsigned long)GetCurrentProcessId()))) {
		log_failure("desktop_name", ERROR_INSUFFICIENT_BUFFER);
		result = 5;
		goto worker_cleanup;
	}
	if (FAILED(StringCchPrintfW(full_desktop_name, 128,
			L"WinSta0\\%s", desktop_name))) {
		log_failure("desktop_name", ERROR_INSUFFICIENT_BUFFER);
		result = 5;
		goto worker_cleanup;
	}
	desktop = CreateDesktopW(desktop_name, 0, 0, 0, GENERIC_ALL, 0);
	if (desktop == 0) {
		error_code = GetLastError();
		log_failure("CreateDesktopW", error_code);
		result = 6;
		goto worker_cleanup;
	}

	if (!SetEnvironmentVariableW(WORKER_MODE_ENV, WORKER_MODE_VALUE) ||
			!SetEnvironmentVariableW(MULTI_CLIENT_ENV,
				MULTI_CLIENT_VALUE) ||
			!SetEnvironmentVariableW(HIDDEN_DESKTOP_ENV,
				HIDDEN_DESKTOP_VALUE) ||
			!SetEnvironmentVariableW(WORKER_READY_MARKER_ENV,
				g_ready_marker)) {
		error_code = GetLastError();
		log_failure("SetEnvironmentVariableW", error_code);
		result = 7;
		goto worker_cleanup;
	}

	if (FAILED(StringCchPrintfW(child_command, 2 * MAX_PATH,
			L"\"%s\" --config engine_config.offline-worker.xml "
			L"--logFilePrefix offline-worker-", game_path))) {
		log_failure("worker_command", ERROR_INSUFFICIENT_BUFFER);
		result = 8;
		goto worker_cleanup;
	}

	ZeroMemory(&startup, sizeof(startup));
	startup.cb = sizeof(startup);
	startup.lpDesktop = full_desktop_name;
	ZeroMemory(&process, sizeof(process));
	child_created = CreateProcessW(game_path, child_command, 0, 0, FALSE,
		CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP, 0, g_root,
		&startup, &process);
	if (!child_created) {
		error_code = GetLastError();
		log_failure("CreateProcessW", error_code);
		result = 10;
		goto worker_cleanup;
	}
	if (!AssignProcessToJobObject(job, process.hProcess)) {
		error_code = GetLastError();
		log_failure("AssignProcessToJobObject", error_code);
		TerminateProcess(process.hProcess, 11);
		WaitForSingleObject(process.hProcess, INFINITE);
		result = 11;
		goto worker_cleanup;
	}
	if (ResumeThread(process.hThread) == (DWORD)-1) {
		error_code = GetLastError();
		log_failure("ResumeThread", error_code);
		TerminateProcess(process.hProcess, 12);
		WaitForSingleObject(process.hProcess, INFINITE);
		result = 12;
	} else if (worker_only) {
		WaitForSingleObject(process.hProcess, INFINITE);
		if (!GetExitCodeProcess(process.hProcess, &child_exit_code)) {
			child_exit_code = 13;
			log_failure("GetExitCodeProcess", GetLastError());
		} else if (child_exit_code != 0) {
			log_failure("worker_process_exit", child_exit_code);
		}
		result = (int)child_exit_code;
	} else if (!wait_for_worker_ready(
			process.hProcess, server_process.hProcess)) {
		result = 23;
	} else {
		/* Keep the hidden authority alive only for the visible host client.
		 * Returning from launch_player means that client has closed or
		 * crashed; terminating the worker job also retires any children on
		 * its private desktop. */
		result = launch_player(game_path, TRUE);
		if (WaitForSingleObject(process.hProcess, 0) == WAIT_TIMEOUT) {
			TerminateJobObject(job, ERROR_PROCESS_ABORTED);
			WaitForSingleObject(process.hProcess, INFINITE);
		}
	}


worker_cleanup:
	SetEnvironmentVariableW(SERVER_LOOPBACK_ENV, 0);
	SetEnvironmentVariableW(SERVER_DATA_ENV, 0);
	SetEnvironmentVariableW(SERVER_HOST_ENV, 0);
	SetEnvironmentVariableW(SERVER_PORT_ENV, 0);
	if (process.hThread != 0) {
		CloseHandle(process.hThread);
	}
	if (process.hProcess != 0) {
		CloseHandle(process.hProcess);
	}
	if (server_process.hThread != 0) {
		CloseHandle(server_process.hThread);
	}
	if (server_process.hProcess != 0) {
		CloseHandle(server_process.hProcess);
	}
	/* Closing the kill-on-close job retires any browser child still using the
	 * private desktop before the desktop handle itself is released. */
	if (job != 0) {
		CloseHandle(job);
	}
	if (desktop != 0) {
		CloseDesktop(desktop);
	}
	if (sockets_started) {
		WSACleanup();
	}
	(void)remove_ready_marker();
	if (singleton != 0) {
		CloseHandle(singleton);
	}
	return result;
}
