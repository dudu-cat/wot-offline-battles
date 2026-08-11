/*
 * World of Tanks 0.8.2 native Filter::input bridge.
 *
 * This module is intentionally pinned to one verified WorldOfTanks.exe.  It
 * exposes one operation that the embedded Python 2.6 API does not: injecting
 * the first complete timestamped pose into a WGVehicleFilter2.  No code or
 * vtable is patched.  Every executable, type, object, vtable and function
 * pointer check must pass before the native virtual method is called.
 */

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>


typedef struct _PyObject {
	long ob_refcnt;
	void *ob_type;
} PyObject;

typedef PyObject *(__cdecl *PyCFunction)(PyObject *, PyObject *);

typedef struct _PyMethodDef {
	const char *ml_name;
	PyCFunction ml_meth;
	int ml_flags;
	const char *ml_doc;
} PyMethodDef;

typedef PyObject *(__cdecl *PyInitModule4Fn)(
	const char *, PyMethodDef *, const char *, PyObject *, int);
typedef int (__cdecl *PyArgParseTupleFn)(PyObject *, const char *, ...);
typedef void (__cdecl *PyErrSetStringFn)(PyObject *, const char *);

typedef struct Vec3 {
	float x;
	float y;
	float z;
} Vec3;

typedef void (__attribute__((thiscall)) *FilterInputFn)(
	void *, double, int, int, const Vec3 *, const Vec3 *, const Vec3 *);


#define PYTHON_API_VERSION_26 1013
#define METH_VARARGS 0x0001

#define EXPECTED_PE_TIMESTAMP 0x50b8eccfU
#define EXPECTED_IMAGE_SIZE 0x0140f000U

#define RVA_PY_INIT_MODULE4 0x00019800U
#define RVA_PY_ARG_PARSE_TUPLE 0x0001d580U
#define RVA_PY_ERR_SET_STRING 0x0000d620U
#define RVA_PY_EXC_TYPE_ERROR_SLOT 0x00e6f3c4U

#define RVA_WG_FILTER2_TYPE 0x00d77fc8U
#define RVA_WG_FILTER2_VTABLE 0x00b67698U
#define RVA_WG_FILTER2_INPUT 0x0050a350U


static unsigned char *g_image_base = 0;


static int bytes_equal(const unsigned char *actual,
		const unsigned char *expected, unsigned int count)
{
	unsigned int index;
	for (index = 0; index < count; ++index) {
		if (actual[index] != expected[index]) {
			return 0;
		}
	}
	return 1;
}


static int readable_region(const void *address, SIZE_T bytes)
{
	MEMORY_BASIC_INFORMATION info;
	const unsigned char *start = (const unsigned char *)address;
	SIZE_T result = VirtualQuery(address, &info, sizeof(info));
	DWORD blocked;
	if (result != sizeof(info) || info.State != MEM_COMMIT) {
		return 0;
	}
	blocked = PAGE_NOACCESS | PAGE_GUARD;
	if ((info.Protect & blocked) != 0) {
		return 0;
	}
	return start + bytes <= (const unsigned char *)info.BaseAddress +
		info.RegionSize;
}


static int executable_region(const void *address)
{
	MEMORY_BASIC_INFORMATION info;
	DWORD protection;
	if (VirtualQuery(address, &info, sizeof(info)) != sizeof(info) ||
			info.State != MEM_COMMIT) {
		return 0;
	}
	protection = info.Protect & 0xffU;
	return protection == PAGE_EXECUTE ||
		protection == PAGE_EXECUTE_READ ||
		protection == PAGE_EXECUTE_READWRITE ||
		protection == PAGE_EXECUTE_WRITECOPY;
}


static int validate_executable(unsigned char *base)
{
	IMAGE_DOS_HEADER *dos;
	IMAGE_NT_HEADERS32 *nt;
	static const unsigned char py_init_signature[] = {
		0x81, 0xec, 0x18, 0x02, 0x00, 0x00, 0xa1
	};
	static const unsigned char py_parse_signature[] = {
		0x51, 0x8b, 0x4c, 0x24, 0x08, 0x8b, 0x54, 0x24
	};
	static const unsigned char filter_input_signature[] = {
		0x83, 0xec, 0x14, 0x53, 0x55, 0x56, 0x8b, 0xf1
	};
	if (!readable_region(base, sizeof(IMAGE_DOS_HEADER))) {
		return 0;
	}
	dos = (IMAGE_DOS_HEADER *)base;
	if (dos->e_magic != IMAGE_DOS_SIGNATURE || dos->e_lfanew <= 0 ||
			dos->e_lfanew > 0x1000) {
		return 0;
	}
	nt = (IMAGE_NT_HEADERS32 *)(base + dos->e_lfanew);
	if (!readable_region(nt, sizeof(IMAGE_NT_HEADERS32)) ||
			nt->Signature != IMAGE_NT_SIGNATURE ||
			nt->FileHeader.Machine != IMAGE_FILE_MACHINE_I386 ||
			nt->FileHeader.TimeDateStamp != EXPECTED_PE_TIMESTAMP ||
			nt->OptionalHeader.SizeOfImage != EXPECTED_IMAGE_SIZE) {
		return 0;
	}
	if (!bytes_equal(base + RVA_PY_INIT_MODULE4, py_init_signature,
			sizeof(py_init_signature)) ||
			!bytes_equal(base + RVA_PY_ARG_PARSE_TUPLE, py_parse_signature,
				sizeof(py_parse_signature)) ||
			!bytes_equal(base + RVA_WG_FILTER2_INPUT, filter_input_signature,
				sizeof(filter_input_signature))) {
		return 0;
	}
	return executable_region(base + RVA_PY_INIT_MODULE4) &&
		executable_region(base + RVA_PY_ARG_PARSE_TUPLE) &&
		executable_region(base + RVA_WG_FILTER2_INPUT);
}


static PyObject *raise_type_error(const char *message)
{
	PyErrSetStringFn set_error;
	PyObject **exception_slot;
	if (g_image_base == 0) {
		return 0;
	}
	set_error = (PyErrSetStringFn)(g_image_base + RVA_PY_ERR_SET_STRING);
	exception_slot = (PyObject **)(g_image_base + RVA_PY_EXC_TYPE_ERROR_SLOT);
	if (readable_region(exception_slot, sizeof(*exception_slot)) &&
			*exception_slot != 0) {
		set_error(*exception_slot, message);
	}
	return 0;
}


static PyObject *seed_filter(PyObject *unused_self, PyObject *args)
{
	PyArgParseTupleFn parse_tuple;
	PyObject *filter_object = 0;
	void *filter_native;
	void **vtable;
	FilterInputFn input;
	double timestamp;
	double x;
	double y;
	double z;
	double roll;
	double pitch;
	double yaw;
	int space_id;
	int entity_id;
	Vec3 position;
	Vec3 position_error;
	Vec3 direction;
	(void)unused_self;

	if (g_image_base == 0) {
		return 0;
	}
	parse_tuple = (PyArgParseTupleFn)(
		g_image_base + RVA_PY_ARG_PARSE_TUPLE);
	if (!parse_tuple(args, "Odiidddddd:seed_filter", &filter_object,
			&timestamp, &space_id, &entity_id, &x, &y, &z,
			&roll, &pitch, &yaw)) {
		return 0;
	}
	if (filter_object == 0 ||
			!readable_region(filter_object, sizeof(PyObject))) {
		return raise_type_error("native bridge received an unreadable object");
	}
	if (filter_object->ob_type != (void *)(
			g_image_base + RVA_WG_FILTER2_TYPE)) {
		return raise_type_error(
			"native bridge requires an exact WGVehicleFilter2 object");
	}
	filter_native = (void *)((unsigned char *)filter_object - 4);
	if (!readable_region(filter_native, 16)) {
		return raise_type_error("native bridge filter body is unreadable");
	}
	vtable = *(void ***)filter_native;
	if (vtable != (void **)(g_image_base + RVA_WG_FILTER2_VTABLE) ||
			!readable_region(vtable, sizeof(void *) * 2)) {
		return raise_type_error("native bridge filter vtable mismatch");
	}
	input = (FilterInputFn)vtable[1];
	if ((void *)input != (void *)(g_image_base + RVA_WG_FILTER2_INPUT) ||
			!executable_region((void *)input)) {
		return raise_type_error("native bridge Filter::input mismatch");
	}

	position.x = (float)x;
	position.y = (float)y;
	position.z = (float)z;
	position_error.x = 0.0f;
	position_error.y = 0.0f;
	position_error.z = 0.0f;
	direction.x = (float)roll;
	direction.y = (float)pitch;
	direction.z = (float)yaw;
	input(filter_native, timestamp, space_id, entity_id,
		&position, &position_error, &direction);

	/* Return the exact filter object as an owned Python reference. */
	filter_object->ob_refcnt += 1;
	return filter_object;
}


static PyMethodDef module_methods[] = {
	{
		"seed_filter", seed_filter, METH_VARARGS,
		"Inject one complete timestamped pose into WGVehicleFilter2."
	},
	{0, 0, 0, 0}
};


__declspec(dllexport) void initoffhangar_native_seed(void)
{
	PyInitModule4Fn init_module;
	unsigned char *base = (unsigned char *)GetModuleHandleA(0);
	if (base == 0 || !validate_executable(base)) {
		return;
	}
	g_image_base = base;
	init_module = (PyInitModule4Fn)(base + RVA_PY_INIT_MODULE4);
	init_module(
		"offhangar_native_seed", module_methods,
		"Version-locked World of Tanks 0.8.2 filter seed bridge.",
		0, PYTHON_API_VERSION_26);
}
