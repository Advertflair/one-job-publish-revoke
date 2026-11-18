import ctypes
import struct
import sys


PAGE_BYTES = 64 * 1024
MAX_MODULE_BYTES = 128 * 1024
MAX_INPUT_BYTES = PAGE_BYTES
MAX_OUTPUT_BYTES = PAGE_BYTES
MEMORY_BYTES = MAX_INPUT_BYTES + MAX_OUTPUT_BYTES
FUEL_BUDGET = 1_000_000
WASM_HEADER = b"\x00asm\x01\x00\x00\x00"
FRAME = struct.Struct("!II")
SUCCESS = b"\x00"


def isolate() -> None:
    if sys.platform != "darwin":
        raise OSError("unsupported worker sandbox platform")
    import resource

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
    library = ctypes.CDLL("/usr/lib/libsandbox.1.dylib")
    library.sandbox_init.argtypes = (ctypes.c_char_p, ctypes.c_uint64,
                                    ctypes.POINTER(ctypes.c_char_p))
    library.sandbox_init.restype = ctypes.c_int
    library.sandbox_free_error.argtypes = (ctypes.c_char_p,)
    library.sandbox_free_error.restype = None
    error = ctypes.c_char_p()
    result = library.sandbox_init(b"(version 1) (deny default)", 0, ctypes.byref(error))
    if result != 0:
        if error.value is not None:
            library.sandbox_free_error(error)
        raise OSError("OS sandbox could not be installed")


def _request() -> tuple[bytes, bytes]:
    frame = sys.stdin.buffer.read(FRAME.size + MAX_MODULE_BYTES + MAX_INPUT_BYTES + 1)
    if len(frame) < FRAME.size:
        raise ValueError("incomplete worker frame")
    wasm_size, input_size = FRAME.unpack_from(frame)
    offset = FRAME.size + wasm_size
    if (
        wasm_size > MAX_MODULE_BYTES
        or input_size > MAX_INPUT_BYTES
        or len(frame) != offset + input_size
        or not frame.startswith(WASM_HEADER, FRAME.size)
    ):
        raise ValueError("invalid worker frame")
    return frame[FRAME.size:offset], frame[offset:]


def _run(runtime, engine, wasm: bytes, document: bytes) -> bytes:
    with runtime.Module(engine, wasm) as module:
        if module.imports:
            return b"imports"
        types = {export.name: export.type for export in module.exports}
        if types.keys() != {"memory", "transform"}:
            return b"exports"
        memory_type = types["memory"]
        if (
            not isinstance(memory_type, runtime.MemoryType)
            or memory_type.is_64
            or memory_type.is_shared
            or memory_type.page_size != PAGE_BYTES
            or memory_type.limits.min != 2
            or memory_type.limits.max != 2
        ):
            return b"memory"
        transform = types["transform"]
        integer = runtime.ValType.i32()
        if (
            not isinstance(transform, runtime.FuncType)
            or transform.params != [integer, integer, integer]
            or transform.results != [integer]
        ):
            return b"signature"
        with runtime.Store(engine) as store:
            store.set_limits(memory_size=MEMORY_BYTES, table_elements=0,
                             instances=1, tables=0, memories=1)
            store.set_fuel(FUEL_BUDGET)
            exports = runtime.Instance(store, module, []).exports(store)
            memory = exports["memory"]
            memory.write(store, document, 0)
            length = exports["transform"](store, len(document), MAX_INPUT_BYTES, MAX_OUTPUT_BYTES)
            if not 0 <= length <= MAX_OUTPUT_BYTES:
                return b"length"
            output = memory.read(store, MAX_INPUT_BYTES, MAX_INPUT_BYTES + length)
            return SUCCESS + bytes(output)


def main() -> bytes:
    try:
        import wasmtime
    except ImportError:
        return b"runtime"
    config = wasmtime.Config()
    config.consume_fuel = True
    config.parallel_compilation = False
    with wasmtime.Engine(config) as engine:
        try:
            isolate()
        except (OSError, AttributeError, ValueError):
            return b"sandbox"
        try:
            wasm, document = _request()
            return _run(wasmtime, engine, wasm, document)
        except ValueError:
            return b"frame"
        except wasmtime.Trap as error:
            return b"fuel" if error.trap_code == wasmtime.TrapCode.OUT_OF_FUEL else b"trap"
        except wasmtime.WasmtimeError:
            return b"module"


if __name__ == "__main__":
    sys.stdout.buffer.write(main())
