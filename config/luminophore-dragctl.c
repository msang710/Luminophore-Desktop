#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc != 2 || (strcmp(argv[1], "begin") != 0 && strcmp(argv[1], "end") != 0 && strcmp(argv[1], "cancel") != 0)) {
        return 2;
    }
    const char *runtime = getenv("XDG_RUNTIME_DIR");
    if (runtime == NULL || *runtime == '\0') {
        return 3;
    }
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    int written = snprintf(address.sun_path, sizeof(address.sun_path), "%s/luminophore-shell/control.sock", runtime);
    if (written < 0 || (size_t)written >= sizeof(address.sun_path)) {
        return 4;
    }
    int connection = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (connection < 0) {
        return 5;
    }
    if (connect(connection, (struct sockaddr *)&address, sizeof(address)) < 0) {
        close(connection);
        return 6;
    }
    char request[96];
    written = snprintf(request, sizeof(request), "{\"command\":\"drag\",\"phase\":\"%s\"}\n", argv[1]);
    if (written < 0 || (size_t)written >= sizeof(request)) {
        close(connection);
        return 7;
    }
    size_t offset = 0;
    while (offset < (size_t)written) {
        ssize_t count = write(connection, request + offset, (size_t)written - offset);
        if (count < 0) {
            if (errno == EINTR) {
                continue;
            }
            close(connection);
            return 8;
        }
        offset += (size_t)count;
    }
    shutdown(connection, SHUT_WR);
    close(connection);
    return 0;
}
