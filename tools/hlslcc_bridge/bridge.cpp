// C entry point over Unity's HLSLcc: DXBC in, Metal Shading Language and
// reflection (JSON) out. Loaded from Python with ctypes.

#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>

#include "hlslcc.h"

namespace {

std::string Escape(const std::string& s) {
    std::string o;
    for (char c : s) {
        switch (c) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\t': o += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    snprintf(buf, sizeof buf, "\\u%04x", c);
                    o += buf;
                } else {
                    o += c;
                }
        }
    }
    return o;
}

class JsonReflection : public HLSLccReflection {
public:
    std::ostringstream diag, inputs, cbs, cbBindings, textures, buffers, outputs, builtins, fragOut;
    bool firstDiag = true, firstInput = true, firstCb = true, firstCbBinding = true, firstTex = true,
         firstBuf = true, firstOut = true, firstBuiltin = true, firstFragOut = true, firstMember = true;
    bool cbOpen = false;
    int threadGroup[3] = {0, 0, 0};

    static const char* Sep(bool& first) {
        if (first) { first = false; return ""; }
        return ",";
    }

    void CloseCb() {
        if (cbOpen) { cbs << "]}"; cbOpen = false; }
    }

    void OnDiagnostics(const std::string& error, int line, bool isError) override {
        diag << Sep(firstDiag) << "{\"msg\":\"" << Escape(error) << "\",\"line\":" << line
             << ",\"error\":" << (isError ? "true" : "false") << "}";
    }
    void OnInputBinding(const std::string& name, int bindIndex) override {
        inputs << Sep(firstInput) << "{\"name\":\"" << Escape(name) << "\",\"loc\":" << bindIndex << "}";
    }
    bool OnConstantBuffer(const std::string& name, size_t bufferSize, size_t memberCount) override {
        CloseCb();
        cbs << Sep(firstCb) << "{\"name\":\"" << Escape(name) << "\",\"size\":" << bufferSize
            << ",\"memberCount\":" << memberCount << ",\"members\":[";
        cbOpen = true;
        firstMember = true;
        return true;
    }
    bool OnConstant(const std::string& name, int bindIndex, SHADER_VARIABLE_TYPE cType, int rows, int cols,
                    bool isMatrix, int arraySize, bool isUsed) override {
        if (!cbOpen) return true;
        cbs << Sep(firstMember) << "{\"name\":\"" << Escape(name) << "\",\"offset\":" << bindIndex
            << ",\"type\":" << static_cast<int>(cType) << ",\"rows\":" << rows << ",\"cols\":" << cols
            << ",\"matrix\":" << (isMatrix ? "true" : "false") << ",\"elements\":" << arraySize
            << ",\"used\":" << (isUsed ? "true" : "false") << "}";
        return true;
    }
    void OnConstantBufferBinding(const std::string& name, int bindIndex) override {
        CloseCb();
        cbBindings << Sep(firstCbBinding) << "{\"name\":\"" << Escape(name) << "\",\"slot\":" << bindIndex << "}";
    }
    void OnTextureBinding(const std::string& name, int bindIndex, int samplerIndex, bool multisampled,
                          HLSLCC_TEX_DIMENSION dim, bool isUAV) override {
        textures << Sep(firstTex) << "{\"name\":\"" << Escape(name) << "\",\"slot\":" << bindIndex
                 << ",\"sampler\":" << samplerIndex << ",\"ms\":" << (multisampled ? "true" : "false")
                 << ",\"dim\":" << static_cast<int>(dim) << ",\"uav\":" << (isUAV ? "true" : "false") << "}";
    }
    void OnBufferBinding(const std::string& name, int bindIndex, bool isUAV) override {
        buffers << Sep(firstBuf) << "{\"name\":\"" << Escape(name) << "\",\"slot\":" << bindIndex
                << ",\"uav\":" << (isUAV ? "true" : "false") << "}";
    }
    void OnThreadGroupSize(unsigned int x, unsigned int y, unsigned int z) override {
        threadGroup[0] = x; threadGroup[1] = y; threadGroup[2] = z;
    }
    void OnVertexProgramOutput(const std::string& name, const std::string& semantic, int semanticIndex) override {
        outputs << Sep(firstOut) << "{\"name\":\"" << Escape(name) << "\",\"semantic\":\"" << Escape(semantic)
                << "\",\"index\":" << semanticIndex << "}";
    }
    void OnBuiltinOutput(SPECIAL_NAME name) override {
        builtins << Sep(firstBuiltin) << static_cast<int>(name);
    }
    void OnFragmentOutputDeclaration(int numComponents, int outputIndex) override {
        fragOut << Sep(firstFragOut) << "{\"components\":" << numComponents << ",\"index\":" << outputIndex << "}";
    }

    std::string Json(int shaderType, int ok) {
        CloseCb();
        std::ostringstream o;
        o << "{\"ok\":" << ok << ",\"shaderType\":" << shaderType << ",\"diagnostics\":[" << diag.str()
          << "],\"inputs\":[" << inputs.str() << "],\"cbuffers\":[" << cbs.str() << "],\"cbBindings\":["
          << cbBindings.str() << "],\"textures\":[" << textures.str() << "],\"buffers\":[" << buffers.str()
          << "],\"vertexOutputs\":[" << outputs.str() << "],\"builtinOutputs\":[" << builtins.str()
          << "],\"fragmentOutputs\":[" << fragOut.str() << "],\"threadGroup\":[" << threadGroup[0] << ","
          << threadGroup[1] << "," << threadGroup[2] << "]}";
        return o.str();
    }
};

char* Dup(const std::string& s) {
    char* p = static_cast<char*>(std::malloc(s.size() + 1));
    std::memcpy(p, s.data(), s.size() + 1);
    return p;
}

}  // namespace

extern "C" int hlslcc_to_metal(const char* dxbc, unsigned int flags, char** outSource, char** outJson) {
    GLSLCrossDependencyData deps;
    HLSLccSamplerPrecisionInfo precisions;
    JsonReflection refl;
    GLSLShader result;
    int ok = 0;
    try {
        ok = TranslateHLSLFromMem(dxbc, flags, LANG_METAL, nullptr, &deps, precisions, refl, &result);
    } catch (...) {
        ok = 0;
        refl.OnDiagnostics("exception during translation", 0, true);
    }
    *outSource = Dup(ok ? result.sourceCode : std::string());
    *outJson = Dup(refl.Json(ok ? result.shaderType : 0, ok));
    return ok;
}

extern "C" void hlslcc_free(char* p) { std::free(p); }
