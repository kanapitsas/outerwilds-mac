// Compile every .metal file given on the command line with the system Metal
// compiler (the same path Unity's player takes) and report failures.
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

int main(int argc, const char* argv[]) {
    @autoreleasepool {
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        if (!dev) { fprintf(stderr, "no Metal device\n"); return 2; }
        MTLCompileOptions* opts = [MTLCompileOptions new];
        opts.fastMathEnabled = YES;
        int failed = 0, ok = 0;
        for (int i = 1; i < argc; i++) {
            NSString* path = [NSString stringWithUTF8String:argv[i]];
            NSString* src = [NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:nil];
            NSError* err = nil;
            id<MTLLibrary> lib = [dev newLibraryWithSource:src options:opts error:&err];
            if (!lib || ![lib newFunctionWithName:@"xlatMtlMain"]) {
                failed++;
                printf("FAIL %s\n%s\n", argv[i], err ? err.localizedDescription.UTF8String : "no xlatMtlMain");
            } else {
                ok++;
            }
        }
        printf("ok %d failed %d\n", ok, failed);
        return failed ? 1 : 0;
    }
}
