final class ScopeCaseA {
    void foo(int inputTick, String text) {
        int fooAccumulator = inputTick + 1;
        handleInt(fooAccumulator);
        handleString(text);
    }

    void bar(int i) {
        int barAccumulator = i + 2;
        handleInt(barAccumulator);
    }

    void handleInt(int value) {
    }

    void handleString(String value) {
    }
}
