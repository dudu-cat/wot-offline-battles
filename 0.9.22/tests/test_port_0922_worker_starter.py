from pathlib import Path
import struct
import unittest


ROOT = Path(__file__).resolve().parents[2]
PORT_ROOT = ROOT / '0.9.22'
SOURCE = PORT_ROOT / 'native' / 'offline_worker_starter.c'
BINARY = PORT_ROOT / 'native' / 'offline_worker_starter.exe'
PLAYER_BATCH = PORT_ROOT / 'START_OFFLINE_0922.bat'
LAN_CLIENT_BATCH = PORT_ROOT / 'START_LAN_CLIENT_0922.bat'
WORKER_BATCH = PORT_ROOT / 'START_SIMULATION_WORKER_0922.bat'


class WorkerStarterTests(unittest.TestCase):
    def test_worker_uses_an_unswitched_private_desktop_and_original_client(self):
        source = SOURCE.read_text(encoding='utf-8')

        self.assertIn('CreateDesktopW(desktop_name', source)
        self.assertIn('startup.lpDesktop = full_desktop_name;', source)
        self.assertIn('L"WinSta0\\\\%s"', source)
        self.assertNotIn('SwitchDesktop(', source)
        self.assertNotIn('SetThreadDesktop(', source)
        self.assertIn('L"WorldOfTanks.exe"', source)
        self.assertIn(
            '--config engine_config.offline-worker.xml', source)
        self.assertNotIn('--preferences', source)
        self.assertIn('--logFilePrefix offline-worker-', source)
        self.assertLess(source.index('CREATE_SUSPENDED'),
                        source.index('AssignProcessToJobObject'))
        self.assertLess(source.index('AssignProcessToJobObject'),
                        source.index('ResumeThread'))
        self.assertIn('JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE', source)

    def test_host_waits_for_worker_and_retires_it_with_the_player(self):
        source = SOURCE.read_text(encoding='utf-8')

        self.assertIn('CreateMutexW(0, TRUE, WORKER_MUTEX_NAME)', source)
        self.assertIn('OFFLINE_LAN_0922_WORKER_READY_MARKER', source)
        self.assertIn(
            'wait_for_worker_ready(\n\t\t\tprocess.hProcess, '
            'server_process.hProcess)', source)
        wait_body = source.split(
            'static int wait_for_worker_ready', 1)[1].split(
                'static int launch_player', 1)[0]
        self.assertIn('worker_exited_before_ready', wait_body)
        self.assertNotIn(
            'failed worker no longer blocks a standalone player',
            wait_body.lower())
        self.assertIn(
            '--config engine_config.offline-player.xml', source)
        self.assertIn('--logFilePrefix offline-player-', source)
        self.assertIn(
            'SetEnvironmentVariableW(WORKER_MODE_ENV, PLAYER_MODE_VALUE)',
            source)
        self.assertIn('lstrcmpiW(command_line, PLAYER_MODE)', source)
        self.assertIn(
            'lstrcmpiW(command_line, PAIRED_PLAYER_MODE)', source)
        self.assertIn('result = launch_player(game_path, TRUE);', source)
        self.assertIn('return launch_player(game_path, FALSE);', source)
        self.assertIn('return launch_player(game_path, TRUE);', source)
        self.assertIn('TerminateJobObject(job, ERROR_PROCESS_ABORTED)', source)
        self.assertLess(source.index('wait_for_worker_ready(\n'),
                        source.index('result = launch_player(game_path, TRUE);'))

    def test_offline_host_owns_a_hidden_loopback_server_before_the_worker(self):
        source = SOURCE.read_text(encoding='utf-8')
        main = source.split('int WINAPI wWinMain', 1)[1]

        server_create = main.index('CreateProcessW(\n\t\t\t\tserver_path')
        server_job = main.index(
            'AssignProcessToJobObject(job, server_process.hProcess)',
            server_create)
        server_ready = main.index(
            'wait_for_local_server(server_process.hProcess)', server_job)
        worker_create = main.index(
            'CreateProcessW(game_path, child_command', server_ready)
        self.assertLess(server_create, server_job)
        self.assertLess(server_job, server_ready)
        self.assertLess(server_ready, worker_create)
        self.assertIn('CREATE_NO_WINDOW', main[server_create:server_job])
        self.assertIn('WOT_0922_LOOPBACK_ONLY', source)
        self.assertIn('OFFLINE_LAN_0922_SERVER_HOST', source)
        self.assertIn('OFFLINE_LAN_0922_SERVER_PORT', source)
        self.assertIn('WOT_0922_SERVER_DATA', source)
        self.assertIn('mods\\\\configs\\\\offline_lan_0922', source)
        self.assertIn('WoT-0.9.22-LAN-Server.exe', source)
        self.assertIn('local_server_port_in_use', source)

    def test_lan_player_clears_process_local_server_override(self):
        source = SOURCE.read_text(encoding='utf-8')
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]

        self.assertIn('SetEnvironmentVariableW(SERVER_HOST_ENV, 0);', launch)
        self.assertIn('SetEnvironmentVariableW(SERVER_PORT_ENV, 0);', launch)

    def test_visible_player_job_tracks_client_process_handoffs(self):
        source = SOURCE.read_text(encoding='utf-8')
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]

        self.assertIn('CreateJobObjectW(0, 0)', launch)
        self.assertIn(
            'AssignProcessToJobObject(player_job, process.hProcess)', launch)
        self.assertIn('JobObjectBasicAccountingInformation', launch)
        self.assertIn('accounting.ActiveProcesses == 0', launch)
        self.assertLess(launch.index('CREATE_SUSPENDED'),
                        launch.index('AssignProcessToJobObject'))
        self.assertLess(launch.index('AssignProcessToJobObject'),
                        launch.index('ResumeThread'))

    def test_paired_player_tracks_handoffs_outside_the_player_job(self):
        source = SOURCE.read_text(encoding='utf-8')
        launch = source.split(
            'static int launch_player', 1)[1].split(
                'int WINAPI wWinMain', 1)[0]
        process_scan = source.split(
            'static int collect_game_processes', 1)[1].split(
                'static int launch_player', 1)[0]

        self.assertIn('CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)',
                      process_scan)
        self.assertIn('QueryFullProcessImageNameW(', process_scan)
        self.assertIn('lstrcmpiW(process_path, game_path) == 0', process_scan)
        self.assertIn('PLAYER_HANDOFF_GRACE_MS', process_scan)
        self.assertIn('process_set_contains(', process_scan)
        self.assertIn('has_new_game_process(', process_scan)
        self.assertIn(
            'has_new_game_process(&current_processes, baseline_processes)',
            process_scan)
        self.assertIn('baseline_collected = collect_game_processes(', launch)
        self.assertIn('wait_for_paired_player_handoff(', launch)
        self.assertLess(
            launch.index('baseline_collected = collect_game_processes('),
            launch.index('CreateProcessW(game_path'))

    def test_duplicate_host_cannot_erase_the_live_worker_ready_marker(self):
        source = SOURCE.read_text(encoding='utf-8')
        main = source.split('int WINAPI wWinMain', 1)[1]

        mutex = main.index(
            'singleton = CreateMutexW(0, TRUE, WORKER_MUTEX_NAME);')
        duplicate = main.index(
            'if (GetLastError() == ERROR_ALREADY_EXISTS)', mutex)
        clear_marker = main.index('if (!remove_ready_marker())', duplicate)
        self.assertLess(mutex, duplicate)
        self.assertLess(duplicate, clear_marker)
        self.assertIn('goto worker_cleanup;', main[clear_marker:])

    def test_ready_marker_is_accepted_only_while_worker_is_alive(self):
        source = SOURCE.read_text(encoding='utf-8')
        wait_body = source.split(
            'static int wait_for_worker_ready', 1)[1].split(
                'static int launch_player', 1)[0]

        first_process_check = wait_body.index(
            'WaitForSingleObject(worker_process, 0)')
        marker_check = wait_body.index(
            'GetFileAttributesW(g_ready_marker)')
        second_process_check = wait_body.index(
            'WaitForSingleObject(worker_process, 0)',
            first_process_check + 1)
        self.assertLess(first_process_check, marker_check)
        self.assertLess(marker_check, second_process_check)
        self.assertIn('worker_exited_after_ready', wait_body)
        self.assertIn('local_server_exited_before_worker_ready', wait_body)

    def test_lan_player_returns_before_any_worker_resource_is_created(self):
        source = SOURCE.read_text(encoding='utf-8')
        main = source.split('int WINAPI wWinMain', 1)[1]

        lan_dispatch = main.index(
            'if (lstrcmpiW(command_line, PLAYER_MODE) == 0)')
        lan_return = main.index(
            'return launch_player(game_path, FALSE);', lan_dispatch)
        worker_mutex = main.index('CreateMutexW(', lan_return)
        worker_desktop = main.index('CreateDesktopW(', worker_mutex)
        worker_process = main.index(
            'CreateProcessW(game_path, child_command', worker_desktop)
        self.assertLess(lan_dispatch, lan_return)
        self.assertLess(lan_return, worker_mutex)
        self.assertLess(worker_mutex, worker_desktop)
        self.assertLess(worker_desktop, worker_process)

    def test_bat_files_only_dispatch_the_gui_starter(self):
        player = PLAYER_BATCH.read_text(encoding='utf-8')
        lan_client = LAN_CLIENT_BATCH.read_text(encoding='utf-8')
        worker = WORKER_BATCH.read_text(encoding='utf-8')

        player_starts = [line.strip() for line in player.splitlines()
                         if line.strip().startswith('start ""')]
        self.assertEqual([
            'start "" "%GAME_ROOT%offline_worker_starter.exe"',
        ], player_starts)
        self.assertIn(
            'start "" "%GAME_ROOT%offline_worker_starter.exe" --player',
            lan_client)
        self.assertIn(
            'start "" "%GAME_ROOT%offline_worker_starter.exe" --worker-only',
            worker)
        self.assertNotIn('powershell.exe', player.lower())
        self.assertNotIn('powershell.exe', lan_client.lower())
        self.assertNotIn('powershell.exe', worker.lower())
        self.assertNotIn('WorldOfTanks.exe --preferences', player)
        self.assertNotIn('WorldOfTanks.exe --preferences', worker)

    def test_built_starter_is_a_32_bit_windows_gui_binary(self):
        payload = BINARY.read_bytes()
        self.assertEqual(b'MZ', payload[:2])
        pe_offset = struct.unpack_from('<I', payload, 0x3c)[0]
        self.assertEqual(b'PE\0\0', payload[pe_offset:pe_offset + 4])
        self.assertEqual(0x14c, struct.unpack_from(
            '<H', payload, pe_offset + 4)[0])
        optional_offset = pe_offset + 24
        self.assertEqual(0x10b, struct.unpack_from(
            '<H', payload, optional_offset)[0])
        self.assertEqual(2, struct.unpack_from(
            '<H', payload, optional_offset + 68)[0])
        self.assertIn(b'CreateDesktopW', payload)
        self.assertIn(b'CreateProcessW', payload)
        self.assertIn(b'CreateToolhelp32Snapshot', payload)
        self.assertIn(b'QueryFullProcessImageNameW', payload)
        self.assertIn('--player'.encode('utf-16le'), payload)
        self.assertIn('--paired-player'.encode('utf-16le'), payload)
        self.assertIn('--worker-only'.encode('utf-16le'), payload)
        self.assertIn(
            'engine_config.offline-player.xml'.encode('utf-16le'), payload)
        self.assertIn(
            'engine_config.offline-worker.xml'.encode('utf-16le'), payload)
        self.assertNotIn('--preferences'.encode('utf-16le'), payload)
        self.assertIn('offline-worker.ready'.encode('utf-16le'), payload)
        self.assertIn(
            'WoT-0.9.22-LAN-Server.exe'.encode('utf-16le'), payload)
        self.assertIn('WOT_0922_LOOPBACK_ONLY'.encode('utf-16le'), payload)
        self.assertIn(b'player_mode', payload)


if __name__ == '__main__':
    unittest.main()
